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


PRESETS = [['Sine wave at 50Hz, 16Channels', {'f' : 1, 'fs' : 10, 'channels' : 16}],
           ['Sine wave at 1kHz, 128Channels', {'f' : 3, 'fs' : 100, 'channels' : 128}]
          ]


class SinusAmp(Amplifier):
    """An amplifier that produces sinus data."""

    def __init__(self):
        self.presets = PRESETS
        self.configure(**self.presets[0][1])
        self.last_sample = time.time()

    @property
    def sample_len(self):
        return 1 / self.fs

    @property
    def elapsed(self):
        return time.time() - self.last_sample

    def get_data(self):
        # simulate blocking until we have enough data
        elapsed = self.elapsed
        if elapsed < self.sample_len:
            time.sleep(self.sample_len - elapsed)
        dt = self.elapsed
        samples = math.floor(self.fs * dt)
        t = np.linspace(self.last_sample, self.last_sample + dt, samples)
        t = np.array([t for i in range(self.channels)]).T
        data = np.sin(np.pi*2*t*self.f)
        self.last_sample = time.time()
        return data, []

    def configure(self, f, fs, channels):
        self.f = f
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
    amp = SinusAmp()
    amp.configure(channels=2, fs=10, f=2)
    amp.start()
    for i in range(20):
        t = time.time()
        print(amp.get_data())
    amp.stop()
#
#    # the same using a context manager, start and stop are called by the
#    # context manager
#    with amp as a:
#        for i in range(10):
#            print(a.get_data())
