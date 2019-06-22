#!/usr/bin/env python

# emotiv.py
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


from Crypto.Cipher import AES
import numpy as np

import usb.core
import usb.util

from libmushu.amplifier import Amplifier


VENDOR_ID = 0x1234
PRODUCT_ID = 0xed02

ENDPOINT_IN = usb.util.ENDPOINT_IN | 2  # second endpoint


class Epoc(Amplifier):

    def __init__(self):
        # find amplifier
        self.dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if self.dev is None:
            raise RuntimeError('Emotiv device is not connected.')
        # pyusb docs say you *have* to call set_configuration, but it does not
        # work unless i *don't* call it.
        #dev.set_configuration()
        # get the serial number
        serial = usb.util.get_string(self.dev, 17, self.dev.iSerialNumber)
        # claim the device and it's two interfaces
        if self.dev.is_kernel_driver_active(0):
            self.dev.detach_kernel_driver(0)
        usb.util.claim_interface(self.dev, 0)
        if self.dev.is_kernel_driver_active(1):
            self.dev.detach_kernel_driver(1)
        usb.util.claim_interface(self.dev, 1)
        # prepare AES
        self.cipher = AES.new(self.generate_key(serial, True))
        # internal states for battery and impedance we have to store since it
        # is not sent with every frame.
        self._battery = 0
        self._quality = [0 for i in range(14)]
        # channel info
        self.channel = ['Counter', 'Battery',
                        'F3', 'FC5', 'AF3', 'F7', 'T7', 'P7', 'O1',
                        'O2', 'P8', 'T8', 'F8', 'AF4', 'FC6', 'F4',
                        'Gyro 1', 'Cyro 2',
                        'Quality F3', 'Quality FC5', 'Quality AF3',
                        'Quality F7', 'Quality T7', 'Quality P7',
                        'Quality 01', 'Quality O2', 'Quality P8',
                        'Quality T8', 'Quality F8', 'Quality AF4',
                        'Quality FC6', 'Quality F4']

    def get_data(self):
        try:
            raw = self.dev.read(ENDPOINT_IN, 32, 1, timeout=1000)
            raw = self.decrypt(raw)
            data = self.parse_raw(raw)
            data = np.array(data)
        except Exception as e:
            print(e)
            data = np.array()
        return data.reshape(1, -1), []

    @staticmethod
    def is_available():
        if usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID) is None:
            return False
        else:
            return True

    def generate_key(self, sn, research=True):
        """Generate the encryption key.

        The key is based on the serial number of the device and the information
        weather it is a research- or consumer device.

        """
        if research:
            key = ''.join([sn[15], b'\x00', sn[14], b'\x54',
                           sn[13], b'\x10', sn[12], b'\x42',
                           sn[15], b'\x00', sn[14], b'\x48',
                           sn[13], b'\x00', sn[12], b'\x50'])
        else:
            key = ''.join([sn[15], b'\x00', sn[14], b'\x48',
                           sn[13], b'\x00', sn[12], b'\x54',
                           sn[15], b'\x10', sn[14], b'\x42',
                           sn[13], b'\x00', sn[12], b'\x50'])
        return key

    def decrypt(self, raw):
        """Decrypt a raw package."""
        data = self.cipher.decrypt(raw[:16]) + self.cipher.decrypt(raw[16:])
        tmp = 0
        for i in range(32):
            tmp = tmp << 8
            tmp += ord(data[i])
        return tmp

    def parse_raw(self, raw):
        """Parse raw data."""
        data = []
        shift = 256
        # 1x counter / battery (8 bit)
        # if the first bit is not set, the remaining 7 bits are the counter
        # otherwise the remaining bits are the battery
        shift -= 8
        tmp = (raw >> shift) & 0b11111111
        if tmp & 0b10000000:
            # battery
            counter = 128
            self._battery = tmp & 0b01111111
        else:
            # counter
            counter = tmp & 0b01111111
        data.append(counter)
        data.append(self._battery)
        # 7x data (14 bit)
        for i in range(7):
            shift -= 14
            data.append((raw >> shift) & 0b11111111111111)
        # 1x quality (14 bit)
        # we assume it is the contact quality for an electrode, the counter
        # gives the number of the electrode. since we only have 14 electrodes
        # we only take the values from counters 0..13 and 64..77
        shift -= 14
        tmp = (raw >> shift) & 0b11111111111111
        if counter < 128:
            if counter % 64 < 14:
                self._quality[counter % 64] = tmp
        # 1x ??? (14 bit)
        shift -= 14
        # 7x data (14 bit)
        for i in range(7):
            shift -= 14
            data.append((raw >> shift) & 0b11111111111111)
        # 2x gyroscope (8 bit)
        # the first bit denotes the sign the remaining 7 bits the number
        for i in range(2):
            shift -= 8
            tmp = (raw >> shift) & 0b01111111
            tmp -= 100
            if (raw >> shift) & 0b10000000:
                tmp *= -1
            data.append(tmp)
        # 1x ??? (8 bit)
        shift -= 8
        data.extend(self._quality)
        return [int(i) for i in data]


if __name__ == '__main__':
    amp = Epoc()
    print('Reading...')
    while 1:
        try:
            print(amp.get_data())
        except Exception as e:
            print(e)
            break
