# ampdecorator.py
# Copyright (C) 2013  Bastian Venthur
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.


"""
This module provides the :class:`AmpDecorator` class.

As a user, it is very unlikely that you'll have to deal with it
directly. Its main purpose is to add additional functionality to the low
level amplifier drivers. This functionality includes features like:
saving data to a file. or being able to receive marker via network
(TCP/IP and UDP).

By using the :func:`libmushu.__init__.get_amp` method, you'll
automatically receive decorated amplifiers.

"""

from __future__ import division

import select
import socket
import time
from multiprocessing import Process, Queue, Event
import os
import struct
import json
import logging
import asyncore
import asynchat

from libmushu.amplifier import Amplifier


logger = logging.getLogger(__name__)
logger.info('Logger started')


END_MARKER = '\n'
BUFSIZE = 2**16
PORT = 12344


class AmpDecorator(Amplifier):
    """This class 'decorates' the Low-Level Amplifier classes with
    Network-Marker and Save-To-File functionality.

    You use it by decorating (not as in Python-Decorator, but in the GoF
    sense) the low level amplifier class you want to use::

        import libmushu
        from libmushu.ampdecorator import AmpDecorator
        from libmushu.driver.randomamp import RandomAmp

        amp = Ampdecorator(RandomAmp)

    Waring: The network marker timings on Windows have a resolution of
    10ms-15ms. On Linux the resolution is 1us. This is due to
    limitations of Python's time.time method, or rather a Windows
    specific issue.

    There exists currently no precise timer, providing times which are
    comparable between two running processes on Windows. The performance
    counter provided on Windows, has a much better resolution but is
    relative to the processes start time and it drifts (1s per 100s), so
    it is only precise for a relatively short amount of time.

    If a higher precision is needed one has to replace the time.time
    calls with something which provides a better precision. For example
    one could create a third process which provides times or regularly
    synchronize both processes with the clock synchronization algorithm
    as described here:

        http://en.wikipedia.org/wiki/Network_Time_Protocol

    Alternatively one could use `timeGetTime` from Windows' Multi Media
    library, which is tunable via `timeBeginPeriod` and provides a
    precision of 1-2ms. Apparently this is the way Chrome and many
    others do it.::

        from __future__ import division

        from ctypes import windll
        import time

        timeBeginPeriod = windll.winmm.timeBeginPeriod
        timeEndPeriod = windll.winmm.timeEndPeriod
        timeGetTime = windll.winmm.timeGetTime

        if __name__ == '__main__':
            # wrap the code that needs high precision in timeBegin- and
            # timeEndPeriod with the same parameter. The parameter is
            # the interval in ms you want as precision. Usually the
            # minimum value allowed is 1 (best).
            timeBeginPeriod(1)
            times = []
            t_start = time.time()
            while time.time() < (time.time() + 1):
                times.append(timeGetTime())
            times = sorted(list(set(times)))
            print(1000 / len(times))
            timeEndPeriod(1)

    """

    def __init__(self, ampcls):
        self.amp = ampcls()
        self.write_to_file = False

    @property
    def presets(self):
        return self.amp.presets

    def start(self, filename=None):
        # prepare files for writing
        self.write_to_file = False
        if filename is not None:
            self.write_to_file = True
            filename_marker = filename + '.marker'
            filename_eeg = filename + '.eeg'
            filename_meta = filename + '.meta'
            for filename in filename_marker, filename_eeg, filename_meta:
                if os.path.exists(filename):
                    logger.error('A file "%s" already exists, aborting.' % filename)
                    raise Exception
            self.fh_eeg = open(filename_eeg, 'wb')
            self.fh_marker = open(filename_marker, 'w')
            self.fh_meta = open(filename_meta, 'w')
            # write meta data
            meta = {'Channels': self.amp.get_channels(),
                    'Sampling Frequency': self.amp.get_sampling_frequency(),
                    'Amp': str(self.amp)
                    }
            json.dump(meta, self.fh_meta, indent=4)

        # start the marker server
        self.marker_queue = Queue()
        self.tcp_reader_running = Event()
        self.tcp_reader_running.set()
        tcp_reader_ready = Event()
        self.tcp_reader = Process(target=marker_reader,
                                  args=(self.marker_queue,
                                        self.tcp_reader_running,
                                        tcp_reader_ready
                                        )
                                  )
        self.tcp_reader.start()
        logger.debug('Waiting for marker server to become ready...')
        tcp_reader_ready.wait()
        logger.debug('Marker server is ready.')
        # zero the sample counter
        self.received_samples = 0
        # start the amp
        self.amp.start()

    def stop(self):
        # stop the amp
        self.amp.stop()
        # stop the marker server
        self.tcp_reader_running.clear()
        logger.debug('Waiting for marker server process to stop...')
        self.tcp_reader.join()
        logger.debug('Marker server process stopped.')
        # close the files
        if self.write_to_file:
            logger.debug('Closing files.')
            for fh in self.fh_eeg, self.fh_marker, self.fh_meta:
                fh.close()

    def configure(self, **kwargs):
        self.amp.configure(**kwargs)

    def get_data(self):
        """Get data from the amplifier.

        This method is supposed to get called as fast as possible (i.e
        hundreds of times per seconds) and returns the data and the
        markers.

        Returns
        -------
        data : 2darray
            a numpy array (time, channels) of the EEG data
        markers : list of (float, str)
            a list of markers. Each element is a tuple of timestamp and
            string. The timestamp is the time in ms relative to the
            onset of the block of data. Note that negative values are
            *allowed* as well as values bigger than the length of the
            block of data returned. That is to be interpreted as a
            marker from the last block and a marker for a future block
            respectively.

        """
        # get data and marker from underlying amp
        data, marker = self.amp.get_data()

        t = time.time()
        # length in sec of the new block according to #samples and fs
        block_duration = len(data) / self.amp.get_sampling_frequency()
        # abs time of start of the block
        t0 = t - block_duration
        # duration of all blocks in ms except the current one
        duration = 1000 * self.received_samples / self.amp.get_sampling_frequency()

        # merge markers
        tcp_marker = []
        while not self.marker_queue.empty():
            m = self.marker_queue.get()
            m[0] = (m[0] - t0) * 1000
            tcp_marker.append(m)
        marker = sorted(marker + tcp_marker)
        # save data to files
        if self.write_to_file:
            for m in marker:
                self.fh_marker.write("%f %s\n" % (duration + m[0], m[1]))
            self.fh_eeg.write(struct.pack("f"*data.size, *data.flatten()))
        self.received_samples += len(data)
        if len(data) == 0 and len(marker) > 0:
            logger.error('Received marker but no data. This is an error, the amp should block on get_data until data is available. Marker timestamps will be unreliable.')
        return data, marker

    def get_channels(self):
        return self.amp.get_channels()

    def get_sampling_frequency(self):
        return self.amp.get_sampling_frequency()


def marker_reader(queue, running, ready):
    """Start the TCP and UDP MarkerServers and start the receiving loop.

    This method runs in a separate process and receives UDP and TCP
    markers. Whenever a marker is received, it is put together with a
    timestamp into a queue.

    After the TCP and UDP servers are set up the ``ready`` event is set
    and the method enters the loop that runs forever until the
    ``running`` Event is cleared. Received markers are put in the
    ``queue``.

    Parameters
    ----------
    queue : Queue
        this queue is used to send markers to a different process
    running : Event
        this event is used to signal this process to terminate its main
        loop
    ready : Event
        this signal is used to signal the "parent"-process that this
        process is ready to receive marker

    """
    MarkerServer(queue, 'udp')
    MarkerServer(queue, 'tcp')
    ready.set()
    while running.is_set():
        asyncore.loop(timeout=5, count=1)


class MarkerServer(asyncore.dispatcher):
    """The marker server.

    It opens a TCP or UDP socket and assigns a :class:`MarkerHandler` to
    the opened socket.

    """

    def __init__(self, queue, proto):
        """Initialize the Server.

        Parameters
        ----------
        queue : multiprocessing.Queue instance
        proto : string
            The protocol to use. Can be either 'tcp' or 'udp'.

        Raises
        ------
        ValueError : if the protocol is unsupported

        """
        asyncore.dispatcher.__init__(self)
        self.queue = queue
        if proto.lower() == 'tcp':
            logger.debug('Opening TCP socket.')
            self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.bind(('', PORT))
            self.listen(5)
        elif proto.lower() == 'udp':
            logger.debug('Opening UDP socket.')
            self.create_socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.bind(('', PORT))
            # in contrast to a TCP socket, an UDP socket has no
            # connection, so the socket is immediately ready to receive
            # data
            handler = MarkerHandler(self.socket, self.queue)
        else:
            raise ValueError('Unsupported protocol: {proto}'.format(proto=proto))

    def handle_accept(self):
        """Accept an incomming TCP connection.

        """
        pair = self.accept()
        if pair is not None:
            sock, addr = pair
            logger.debug('Incoming connection from {addr}'.format(addr=addr))
            handler = MarkerHandler(sock, self.queue)


class MarkerHandler(asynchat.async_chat):
    """Handler for incoming data streams.

    This handler processes incoming data from a TCP or UDP sockets. Each
    packet ends with a terminator character sequence. The handler takes
    care of incomplete packets and puts complete packets in the queue.

    """

    def __init__(self, socket, queue):
        """Initialize the Handler.

        Parameters
        ----------
        socket : socket.socket
            the socket can be TCP or UDP. In case of UDP the socket must
            be binded already, the TCP socket must be an opened
            connection (i.e. after accept)
        queue : multiprocessing.Queue instance
            The queue to send the received markers to.

        """
        asynchat.async_chat.__init__(self, socket)
        self.set_terminator(END_MARKER)
        self.data = ''
        self.timestamp = None
        self.queue = queue

    def handle_close(self):
        logger.debug('Connection closed by peer, closing connection.')
        self.close()

    def writable(self):
        """Signal weather the socket is ready to send data.

        Returns
        -------
        writable : bool
            ready to send or not

        """
        # if we don't set the writable flag to false, the UDP socket
        # will signal that it is ready to send data on every iteration
        # of the asycore loop, which will cause massive CPU strain. this
        # is not the case for TCP sockets, but doesn't hurt either.
        return False

    def collect_incoming_data(self, data):
        """Got potentially partial data packet.

        This method collects potentially incomplete data packets and
        records the timestamp when the first part of the incomplete data
        packet arrived.

        Parameters
        ----------
        data : str
            the data packet

        """
        if self.timestamp is None:
            self.timestamp = time.time()
        #logger.debug('Received maybe incomlete data: {data}'.format(data=data))
        self.data = self.data + data

    def found_terminator(self):
        """Found a complete packet.

        A complete data packet has arrived. Put the data packet with its
        timestamp in the queue. And reset the timestamp.

        """
        # to something with data
        #logger.debug('Received {data}'.format(data=self.data))
        self.queue.put([self.timestamp, self.data])
        self.data = ''
        self.timestamp = None

    def handle_error(self):
        """An error occurred.

        """
        logger.error('An error occurred.')
        self.close()
        # the default implementation prints a condensed tracebackk which
        # is not useful at all, so we re-raise the exception
        raise

