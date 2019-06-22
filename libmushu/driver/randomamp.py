# randomamp.py
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

from __future__ import division

import time
import math
import json

import numpy as np

from libmushu.amplifier import Amplifier


PRESETS = [
        ['Random noise at 50Hz, 16Channels',
            {'fs' : 50, 'channels' : 16}],
        ['Random noise at 1kHz, 128Channels',
            {'fs' : 1000, 'channels' : 128}]
        ]


class RandomAmp(Amplifier):
    """An amplifier that produces random data."""

    def __init__(self):
        self.presets = PRESETS
        self.channels = 17
        self.fs = 100

    def start(self):
        self.last_sample = time.time()

    @property
    def sample_len(self):
        return 1 / self.fs

    def get_data(self):
        # simulate blocking until we have enough data
        elapsed = time.time() - self.last_sample
        if elapsed < self.sample_len:
            time.sleep(self.sample_len - elapsed)
        # ready
        dt = time.time() - self.last_sample
        samples = math.floor(self.fs * dt)
        # actual time according to number of samples we're sending out
        dt = samples / self.fs
        data = np.random.randint(0, 1024, (samples, self.channels))
        self.last_sample += dt
        return data, []

    def configure(self, fs, channels):
        self.fs = fs
        self.channels = channels

    def get_channels(self):
        return ['Ch_%d' % i for i in range(self.channels)]

    def get_sampling_frequency(self):
        return self.fs

    @staticmethod
    def is_available():
        return True


if __name__ == '__main__':
    pass
#    amp = RandomAmp()
#    amp.start()
#    for i in range(10):
#        t = time.time()
#        print(amp.get_data())
#        print()
#        print("FS:", 1 / (time.time() - t))
#        print()
#    amp.stop()
#
#    # the same using a context manager, start and stop are called by the
#    # context manager
#    with amp as a:
#        for i in range(10):
#            print(a.get_data())
