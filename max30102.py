import time
from smbus2 import SMBus

I2C_ADDRESS = 0x57
REG_FIFO_CONFIG = 0x08
REG_MODE_CONFIG = 0x09
REG_SPO2_CONFIG = 0x0A
REG_LED1_PA     = 0x0C
REG_LED2_PA     = 0x0D
REG_FIFO_DATA   = 0x07

class MAX30102:
    def __init__(self, bus_num=1):
        self.bus = SMBus(bus_num)
        self.reset()
        time.sleep(0.1)
        self.setup()

    def reset(self):
        try:
            self.bus.write_byte_data(I2C_ADDRESS, REG_MODE_CONFIG, 0x40)
        except Exception:
            pass

    def setup(self):
        try:
            self.bus.write_byte_data(I2C_ADDRESS, REG_FIFO_CONFIG, 0x4F)
            self.bus.write_byte_data(I2C_ADDRESS, REG_MODE_CONFIG, 0x03)
            self.bus.write_byte_data(I2C_ADDRESS, REG_SPO2_CONFIG, 0x27)
            self.bus.write_byte_data(I2C_ADDRESS, REG_LED1_PA, 0x24)
            self.bus.write_byte_data(I2C_ADDRESS, REG_LED2_PA, 0x24)
        except Exception as e:
            print(f"MAX30102 Init Error: {e}")

    def read_raw(self):
        try:
            d = self.bus.read_i2c_block_data(I2C_ADDRESS, REG_FIFO_DATA, 6)
            red = ((d[0] << 16) | (d[1] << 8) | d[2]) & 0x03FFFF
            ir  = ((d[3] << 16) | (d[4] << 8) | d[5]) & 0x03FFFF
            return red, ir
        except Exception:
            return 0, 0
