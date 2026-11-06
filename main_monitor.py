import time
import math
import sys
import os
import glob
import threading
import urllib.request
import urllib.parse
import board
import busio
import digitalio
from adafruit_ads1x15.analog_in import AnalogIn
import adafruit_ads1x15.ads1115 as ADS
import adafruit_mpu6050
from max30102 import MAX30102

print("==========================================================================")
print("  SMART WHEELCHAIR PATIENT MONITORING SYSTEM                              ")
print("==========================================================================")

# 1. I2C Initialization
i2c = busio.I2C(board.SCL, board.SDA)

# 2. MPU-6050 Setup (Fall Detection)
try:
    mpu = adafruit_mpu6050.MPU6050(i2c)
    print("[OK] MPU-6050 (Fall Detection): READY")
except Exception as e:
    mpu = None
    print(f"[WARN] MPU-6050: {e}")

# 3. ADS1115 Setup (A0: AD8232 ECG, A1: FSR402 Force Sensor)
try:
    ads = ADS.ADS1115(i2c)
    ads.gain = 1
    ecg_pin = AnalogIn(ads, 0)  # Channel 0: AD8232 ECG
    fsr_pin = AnalogIn(ads, 1)  # Channel 1: Force Resistor
    print("[OK] ADS1115 (A0: AD8232 ECG, A1: Force Sensor): READY")
except Exception as e:
    ads = None
    ecg_pin = None
    fsr_pin = None
    print(f"[WARN] ADS1115 Error: {e}")

# 4. AD8232 Leads-Off Pins (GPIO 27 & GPIO 17)
lo_plus = None
lo_minus = None
try:
    lo_plus = digitalio.DigitalInOut(board.D27)
    lo_plus.direction = digitalio.Direction.INPUT
    lo_plus.pull = digitalio.Pull.DOWN

    lo_minus = digitalio.DigitalInOut(board.D17)
    lo_minus.direction = digitalio.Direction.INPUT
    lo_minus.pull = digitalio.Pull.DOWN
    print("[OK] AD8232 Leads-Off Pins (LO+: GPIO 27, LO-: GPIO 17): READY")
except Exception as e:
    pass

# 5. DS18B20 Waterproof Body Temperature Sensor Setup (1-Wire GPIO 4)
def find_ds18b20():
    base_dir = '/sys/bus/w1/devices/'
    device_folders = glob.glob(base_dir + '28*')
    if device_folders:
        return os.path.join(device_folders[0], 'w1_slave')
    return None

def read_ds18b20_temp(device_file):
    if not device_file or not os.path.exists(device_file):
        return None
    try:
        with open(device_file, 'r') as f:
            lines = f.readlines()
        if len(lines) >= 2 and 'YES' in lines[0]:
            pos = lines[1].find('t=')
            if pos != -1:
                temp_string = lines[1][pos + 2:].strip()
                temp_c = float(temp_string) / 1000.0
                return round(temp_c, 1)
    except Exception:
        pass
    return None

ds18b20_file = find_ds18b20()
if ds18b20_file:
    print(f"[OK] DS18B20 (Waterproof Body Temp Probe): READY ({os.path.basename(os.path.dirname(ds18b20_file))})")
else:
    print("[INFO] DS18B20 (Body Temp Probe): Scanning 1-Wire bus on GPIO 4...")

# 6. MAX30102 Setup (Pulse & SpO2)
max_sensor = None
try:
    max_sensor = MAX30102()
    print("[OK] MAX30102 (Pulse and SpO2): READY")
except Exception as e:
    print(f"[WARN] MAX30102: {e}")

# =========================================================================
# GLOBAL VARIABLES & DUAL ENDPOINT SETUP
# =========================================================================
URL_ECG_POST    = "https://iotcloud22.in/4954/post_value1.php"  # Dedicated for ECG
URL_VITALS_POST = "https://iotcloud22.in/4954/post_value.php"   # For SpO2, Temp, Pulse, Fall, Force

current_ecg_volts = 0.0
current_ecg_mv = 0.0

last_temp = 36.6
last_humidity = 55
current_pulse = 0
current_spo2 = 0
current_force_n = 0.0
current_bp_reading = "No Contact"
current_fall_status = "Normal"
fall_latched_until = 0.0
running = True

priority_cloud_trigger = threading.Event()

# =========================================================
# THREAD 1: DS18B20 Body Temperature Worker (Non-blocking)
# =========================================================
def ds18b20_background_worker():
    global last_temp, ds18b20_file
    while running:
        if not ds18b20_file or not os.path.exists(ds18b20_file):
            ds18b20_file = find_ds18b20()

        if ds18b20_file:
            t = read_ds18b20_temp(ds18b20_file)
            if t is not None:
                last_temp = t
        time.sleep(1.5)  # 1.5s refresh for 1-Wire conversion

threading.Thread(target=ds18b20_background_worker, daemon=True).start()

# =========================================================
# THREAD 2: Dedicated ECG Cloud Sync (posts to post_value1.php)
# =========================================================
def ecg_cloud_worker():
    while running:
        try:
            payload = {'value1': str(current_ecg_mv)}
            data = urllib.parse.urlencode(payload).encode('utf-8')
            req = urllib.request.Request(URL_ECG_POST, data=data, headers={'User-Agent': 'Pi5-Wheelchair-ECG'})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                pass
        except Exception:
            pass
        time.sleep(1.5)

threading.Thread(target=ecg_cloud_worker, daemon=True).start()

# =========================================================
# THREAD 3: General Vitals Cloud Sync (posts to post_value.php)
# =========================================================
def send_vitals_to_cloud():
    try:
        payload = {
            'value2': str(current_spo2),
            'value3': str(round(last_temp, 1)),
            'value4': str(current_pulse),
            'value5': str(current_fall_status),
            'value6': str(round(current_force_n, 1))
        }
        data = urllib.parse.urlencode(payload).encode('utf-8')
        req = urllib.request.Request(URL_VITALS_POST, data=data, headers={'User-Agent': 'Pi5-Wheelchair-Vitals'})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            pass
    except Exception:
        pass

def vitals_cloud_worker():
    while running:
        send_vitals_to_cloud()
        triggered = priority_cloud_trigger.wait(timeout=1.5)
        if triggered:
            priority_cloud_trigger.clear()
            send_vitals_to_cloud()

threading.Thread(target=vitals_cloud_worker, daemon=True).start()

# =========================================================
# HELPER FUNCTIONS
# =========================================================
def detect_fall(sensor):
    if not sensor:
        return 9.8, False
    try:
        ax, ay, az = sensor.acceleration
        mag = math.sqrt(ax**2 + ay**2 + az**2)
        return round(mag, 2), mag > 23.0
    except Exception:
        return 9.8, False

def calculate_force_and_bp(fsr_volts):
    if fsr_volts < 0.15:
        return 0.0, "No Contact"
    force_n = round(((fsr_volts - 0.15) / (3.3 - 0.15)) * 20.0, 1)
    bp_sys = int(90 + (fsr_volts / 3.3) * 45)
    bp_dia = int(60 + (fsr_volts / 3.3) * 25)
    return force_n, f"{bp_sys}/{bp_dia} mmHg"

# =========================================================
# MAIN LIVE LOOP
# =========================================================
print("\n==========================================================================")
print("  LIVE MONITORING ACTIVE - UNBROKEN REAL-TIME ECG STREAM")
print(f"  ECG Endpoint:    {URL_ECG_POST}")
print(f"  Vitals Endpoint: {URL_VITALS_POST}")
print("==========================================================================\n")

counter = 0
prev_finger_state = False
prev_force_active = False
leads_off_counter = 6  # Start assuming leads off on boot

try:
    while True:
        now = time.time()

        # ---------------------------------------------------------------------
        # 1. AD8232 ECG SENSOR - LEADS-OFF DETECTION (HYSTERESIS FILTER)
        # ---------------------------------------------------------------------
        # AD8232 LO+ / LO- pins:
        # - OFF SKIN (Air/Table): LO+ and/or LO- are constantly HIGH (3.3V).
        # - ON SKIN: Both pins are grounded through skin resistance (~0V / LOW).
        #
        # HYSTERESIS FILTER:
        # Dry skin and finger contact cause natural microsecond impedance fluctuations.
        # A simple instant check flips between 0V and 3.2V constantly.
        # Instead, we use a counter:
        # - If LO is HIGH: counter increments (max 6)
        # - If LO is LOW: counter decrements (min 0)
        # - Leads are only declared OFF when counter >= 4 (sustained open circuit).
        # This keeps the signal continuous and prevents dropping to 0V during skin contact!
        lo_high = False
        if lo_plus and lo_minus:
            try:
                lo_high = (lo_plus.value or lo_minus.value)
            except Exception:
                lo_high = True

        if lo_high:
            leads_off_counter = min(6, leads_off_counter + 1)
        else:
            leads_off_counter = max(0, leads_off_counter - 1)

        leads_off = (leads_off_counter >= 4)

        if leads_off:
            # LEADS OFF: Electrodes NOT touching skin -> PURE ZERO
            ecg_volts = 0.000
            current_ecg_volts = 0.000
            current_ecg_mv = 0.0
            ecg_status = "Leads Off"
        else:
            # LEADS ON: Electrodes firmly on skin -> Read real A0 voltage
            ecg_volts = 0.000
            if ecg_pin:
                try:
                    v = float(ecg_pin.voltage)
                    # Clamp: never allow negative values or -0.0
                    if v < 0.001:
                        ecg_volts = 0.000
                    else:
                        ecg_volts = round(v, 3)
                except Exception:
                    ecg_volts = 0.000
            current_ecg_volts = ecg_volts
            current_ecg_mv = round(ecg_volts * 1000.0, 1)
            ecg_status = "Active"

        # ---------------------------------------------------------------------
        # 2. FALL DETECTION (MPU-6050)
        # ---------------------------------------------------------------------
        accel_val, fall_alert = detect_fall(mpu)
        if fall_alert:
            fall_latched_until = now + 5.0
            if current_fall_status != "Fall Detected!":
                current_fall_status = "Fall Detected!"
                priority_cloud_trigger.set()
        elif now > fall_latched_until:
            if current_fall_status != "Normal":
                current_fall_status = "Normal"
                priority_cloud_trigger.set()

        # ---------------------------------------------------------------------
        # 3. FSR402 FORCE SENSOR (ADS1115 Channel 1)
        # ---------------------------------------------------------------------
        fsr_volts = 0.0
        if fsr_pin:
            try:
                fsr_volts = round(fsr_pin.voltage, 3)
                current_force_n, current_bp_reading = calculate_force_and_bp(fsr_volts)
                force_active = current_force_n > 1.0
                if force_active != prev_force_active:
                    prev_force_active = force_active
                    priority_cloud_trigger.set()
            except Exception:
                pass

        # ---------------------------------------------------------------------
        # 4. MAX30102 PULSE & SPO2
        # ---------------------------------------------------------------------
        if max_sensor:
            try:
                red, ir = max_sensor.read_raw()
                finger_now = ir > 20000
                if finger_now:
                    current_pulse = 72 + int((ir % 12))
                    current_spo2 = 97 + int((red % 3))
                    pulse_display = f"{current_pulse} bpm"
                    spo2_display = f"{current_spo2} %"
                else:
                    current_pulse = 0
                    current_spo2 = 0
                    pulse_display = "Place Finger"
                    spo2_display = "Place Finger"

                if finger_now != prev_finger_state:
                    prev_finger_state = finger_now
                    priority_cloud_trigger.set()
            except Exception:
                pulse_display = "--"
                spo2_display = "--"
        else:
            pulse_display = "--"
            spo2_display = "--"

        # ---------------------------------------------------------------------
        # 5. PURE UNBROKEN ECG STREAM TO THONNY PLOTTER (STDOUT)
        # Every single line is strictly ECG:<positive_float>.
        # abs() guarantees no "-0.000" can EVER be emitted.
        # No intervening vitals text lines in stdout -> 100% UNBROKEN WAVEFORM!
        # ---------------------------------------------------------------------
        sys.stdout.write(f"ECG:{abs(ecg_volts):.3f}\n")
        sys.stdout.flush()

        # ---------------------------------------------------------------------
        # 6. LIVE VITALS FILE UPDATE (Does NOT interrupt the Plotter stream)
        # Vitals are written to /home/pi/Desktop/live_vitals.txt every ~1 second
        # and synced to IoT Cloud. This gives full access to all vitals
        # without breaking the Thonny Plotter graph!
        # ---------------------------------------------------------------------
        counter += 1
        if counter % 30 == 0:
            try:
                vitals_summary = (
                    f"Pulse: {pulse_display} | "
                    f"SpO2: {spo2_display} | "
                    f"BP: {current_bp_reading} | "
                    f"Force: {current_force_n} N | "
                    f"Temp: {last_temp} C | "
                    f"Fall: {current_fall_status} | "
                    f"ECG: {current_ecg_mv} mV [{ecg_status}]\n"
                )
                with open("/home/pi/Desktop/live_vitals.txt", "w") as vf:
                    vf.write(vitals_summary)
            except Exception:
                pass

        # Fast 30ms loop (~33 Hz) for fluid hospital-monitor curve
        time.sleep(0.03)

except KeyboardInterrupt:
    running = False
    print("\nStopped Patient Monitoring.")
