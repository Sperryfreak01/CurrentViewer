#!/usr/bin/env python
# Copyright (c) Marius Gheorghescu. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for full license information.
import sys
import time
import serial
import serial.tools.list_ports
import logging
from logging.handlers import RotatingFileHandler
import argparse
import platform
import collections
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import mplcursors
import matplotlib.animation as animation
from matplotlib.dates import num2date, MinuteLocator, SecondLocator, DateFormatter
from matplotlib.widgets import Button
from datetime import datetime, timedelta
from threading import Thread
import os
import configparser
import inspect
import easygui


version = '1.0.7'

port = ''
#baud = 115200

#logfile = 'current_viewer.log'

#refresh_interval = 66 # 66ms = 15fps

# controls the window size (and memory usage). 100k samples = 3 minutes
#buffer_max_samples = 100000

# controls how many samples to display in the chart (and CPU usage). Ie 4k display should be ok with 2k samples
#chart_max_samples = 2048

# how many samples to average (median)
#max_supersampling = 16;

# set to true to compute median instead of average (less noise, more CPU)
#median_filter = 0;

# 
save_file = None;
save_format = None;

connected_device = "CurrentRanger"


#########################################################################################################
Config = configparser.ConfigParser()

if sys.platform.startswith('win'):
    filename = inspect.getframeinfo(inspect.currentframe()).filename
    settingsFilename = os.path.join(os.path.dirname(os.path.abspath(filename)), 'Config.txt')
else:
    settingsFilename = os.path.join(sys.path[0], 'Config.txt')

Config.read(settingsFilename)
logger = logging.getLogger("main_logger")
# disable matplotlib logging for fonts, seems to be quite noisy
logging.getLogger('matplotlib.font_manager').disabled = True
#########################################################################################################

def ConfigSectionMap(section):
    dict1 = {}
    options = Config.options(section)
    for option in options:
        try:
            dict1[option] = Config.get(section, option)
            if dict1[option] == -1:
                logger.debug("skip: %s" % option)
        except:
            print("exception on %s!" % option)
            dict1[option] = None
    return dict1

def configure_logging(location, name, level, logger):
    logDIR = ConfigSectionMap("Configuration")['log_location']
    logFormatter = logging.Formatter("%(asctime)s [%(levelname)s]  %(message)s")
    if not os.path.exists(logDIR):
        os.makedirs(logDIR)
    logname = '{3}/{0}-{1}-{2}.txt'.format(location, name, datetime.strftime(datetime.today(), '%d-%m-%Y'), logDIR)
    if os.path.isfile(logname):
        newlog = False
    else:
        newlog = True

    if level == 'INFO':
        print('got into info area')
        fileHandler = logging.FileHandler(logname)
        fileHandler.setLevel(logging.INFO)
        fileHandler.setFormatter(logFormatter)

    elif level == 'DEBUG':
        fileHandler = logging.FileHandler(logname)
        fileHandler.setLevel(logging.DEBUG)
        fileHandler.setFormatter(logFormatter)
        logger.addHandler(fileHandler)

        logging.basicConfig(filename='debug.log', level=logging.DEBUG)
        consoleHandler = logging.StreamHandler(sys.stdout)
        consoleHandler.setLevel(logging.DEBUG)
        consoleHandler.setFormatter(logFormatter)
        logger.addHandler(consoleHandler)


# !/usr/bin/python3
class ReadLine:
    def __init__(self, s):
        self.buf = bytearray()
        self.s = s

    def readline(self):
        i = self.buf.find(b"\n")
        if i >= 0:
            r = self.buf[:i + 1]
            self.buf = self.buf[i + 1:]
            return r
        while True:
            i = max(1, min(2048, self.s.in_waiting))
            data = self.s.read(i)
            i = data.find(b"\n")
            if i >= 0:
                r = self.buf + data[:i + 1]
                self.buf[0:] = data[i + 1:]
                return r
            else:
                self.buf.extend(data)


class CRPlot:
    def __init__(self, sample_buffer = 100):
        self.port = '/dev/ttyACM0'
        self.baud = 9600
        self.thread = None
        self.stream_data = True
        self.pause_chart = False
        self.sample_count = 0
        self.animation_index = 0
        self.max_samples = sample_buffer
        self.data = collections.deque(maxlen=sample_buffer)
        self.timestamps = collections.deque(maxlen=sample_buffer)
        self.dataStartTS = None
        self.serialConnection = None
        self.framerate = 30

    def serialStart(self, port, speed = 115200):
        self.port = port
        self.baud = speed
        logging.info("Trying to connect to port='{}' baud='{}'".format(port, speed))
        try:
            self.serialConnection = serial.Serial(self.port, self.baud, timeout=5)
            logging.info("Connected to {} at baud {}".format(port, speed))
        except serial.SerialException as e:
            logging.error("Error connecting to serial port: {}".format(e))
            return False
        except:
            logging.error("Error connecting to serial port, unexpected exception:{}".format(sys.exc_info()))
            return False

        if self.thread == None:
            self.thread = Thread(target=self.serialStream)
            self.thread.start()

            print('Initializing data capture:', end='')
            wait_timeout = 100
            while wait_timeout > 0 and self.sample_count == 0:
                print('.', end='', flush=True)
                time.sleep(0.01)
                wait_timeout -= 1

            if (self.sample_count == 0):
                logging.error("Error: No data samples received. Aborting")
                return False

            print("OK\n")
            return True


    def pauseRefresh(self, state):
        logging.debug("pause {}".format(state))
        self.pause_chart = not self.pause_chart
        if self.pause_chart:
            self.ax.set_title('<Paused>', color="yellow")
            self.bpause.label.set_text('Resume')
        else:
            self.ax.set_title(f"Streaming: {connected_device}", color="white")
            self.bpause.label.set_text('Pause')

    def saveAnimation(self, state):
        logging.debug("save {}".format(state))

        self.bsave.label.set_text('Saving...')
        plt.gcf().canvas.draw()
        filename = None
        while True:
            filename = 'current' + str(self.animation_index) + '.gif'
            self.animation_index += 1
            if not path.exists(filename):
                break
        logging.info("Animation saved to '{}'".format(filename))
        self.anim.save(filename, writer='imagemagick', fps=self.framerate)
        self.bsave.label.set_text('GIF')

    def chartSetup(self, refresh_interval=100):
        plt.style.use('dark_background')
        fig = plt.figure(num=f"Current Viewer {version}", figsize=(10, 6))
        self.ax = plt.axes()
        ax = self.ax

        ax.set_title(f"Streaming: {connected_device}", color="white")

        fig.text (0.2, 0.88, f"CurrentViewer {version}", color="yellow",  verticalalignment='bottom', horizontalalignment='center', fontsize=9, alpha=0.7)
        fig.text (0.89, 0.0, f"github.com/MGX3D/CurrentViewer", color="white",  verticalalignment='bottom', horizontalalignment='center', fontsize=9, alpha=0.5)

        ax.set_ylabel("Current draw (Amps)")
        ax.set_yscale("log", nonpositive='clip')
        ax.set_ylim(1e-10, 1e1)
        plt.yticks([1.0e-9, 1.0e-8, 1.0e-7, 1.0e-6, 1.0e-5, 1.0e-4, 1.0e-3, 1.0e-2, 1.0e-1, 1.0], ['1nA', '10nA', '100nA', '1\u00B5A', '10\u00B5A', '100\u00B5A', '1mA', '10mA', '100mA', '1A'], rotation=0)
        ax.grid(axis="y", which="both", color="yellow", alpha=.3, linewidth=.5)

        ax.set_xlabel("Time")
        plt.xticks(rotation=20)
        ax.set_xlim(datetime.now(), datetime.now() + timedelta(seconds=10))
        ax.grid(axis="x", color="green", alpha=.4, linewidth=2, linestyle=":")

        #ax.xaxis.set_major_locator(SecondLocator())
        ax.xaxis.set_major_formatter(DateFormatter('%H:%M:%S'))

        def on_xlims_change(event_ax):
            logging.debug("Interactive zoom: {} .. {}".format(num2date(event_ax.get_xlim()[0]), num2date(event_ax.get_xlim()[1])))

            chart_len = num2date(event_ax.get_xlim()[1]) - num2date(event_ax.get_xlim()[0])

            if chart_len.total_seconds() < 5:
                self.ax.xaxis.set_major_formatter(DateFormatter('%H:%M:%S.%f'))
            else:
                self.ax.xaxis.set_major_formatter(DateFormatter('%H:%M:%S'))
                self.ax.xaxis.set_minor_formatter(DateFormatter('%H:%M:%S.%f'))

        ax.callbacks.connect('xlim_changed', on_xlims_change)

        lines = ax.plot([], [], label="Current")[0]

        lastText = ax.text(0.50, 0.95, '', transform=ax.transAxes)
        statusText = ax.text(0.50, 0.50, '', transform=ax.transAxes)
        self.anim = animation.FuncAnimation(fig, self.getSerialData, fargs=(lines, plt.legend(), lastText), interval=refresh_interval)

        plt.legend(loc="upper right", framealpha=0.5)

        apause = plt.axes([0.91, 0.15, 0.08, 0.07])
        self.bpause = Button(apause, label='Pause', color='0.2', hovercolor='0.1')
        self.bpause.on_clicked(self.pauseRefresh)
        self.bpause.label.set_color('yellow')

        aanimation = plt.axes([0.91, 0.25, 0.08, 0.07])
        self.bsave = Button(aanimation, 'GIF', color='0.2', hovercolor='0.1')
        self.bsave.on_clicked(self.saveAnimation)
        self.bsave.label.set_color('yellow')

        crs = mplcursors.cursor(ax, hover=True)
        @crs.connect("add")
        def _(sel):
            sel.annotation.arrow_patch.set(arrowstyle="simple", fc="yellow", alpha=.4)
            sel.annotation.set_text(self.textAmp(sel.target[1]))

        self.framerate = 1000/refresh_interval
        plt.gcf().autofmt_xdate()
        plt.show()


    def serialStream(self):

        # set data streaming mode on CR (assuming it was off)
        self.serialConnection.write(b'u')

        self.serialConnection.reset_input_buffer()
        self.sample_count = 0
        line_count = 0
        error_count = 0
        self.dataStartTS = datetime.now()

        # data timeout threshold (seconds) - bails out of no samples received
        data_timeout_ths = 0.5

        line = None
        device_data = bytearray()

        logging.info("Starting USB streaming loop")

        while (self.stream_data):
            try:
                # get the timestamp before the data string, likely to align better with the actual reading
                ts = datetime.now()

                chunk_len = device_data.find(b"\n")
                if chunk_len >= 0:
                    line = device_data[:chunk_len]
                    device_data = device_data[chunk_len+1:]
                else:
                    line = None
                    while line == None and self.stream_data:
                        chunk_len = max(1, min(4096, self.serialConnection.in_waiting))
                        chunk = self.serialConnection.read(chunk_len)
                        chunk_len = chunk.find(b"\n")
                        if chunk_len >= 0:
                            line = device_data + chunk[:chunk_len]
                            device_data[0:] = chunk[chunk_len+1:]
                        else:
                            device_data.extend(chunk)

                if line == None:
                    continue

                line = line.decode(encoding="ascii", errors="strict")

                # TODO remove this, not needed in the new setup
                if (line.startswith("USB_LOGGING")):
                    if (line.startswith("USB_LOGGING_DISABLED")):
                        # must have been left open by a different process/instance
                        logging.info("CR USB Logging was disabled. Re-enabling")
                        self.serialConnection.write(b'u')
                        self.serialConnection.flush()
                    continue

                data = float(line)
                self.sample_count += 1
                line_count += 1

                if save_file:
                    if save_format == 'CSV':
                        save_file.write(f"{ts},{data}\n")
                    elif save_format == 'JSON':
                        save_file.write("{}{{\"time\":\"{}\",\"amps\":\"{}\"}}".format(',\n' if self.sample_count>1 else '', ts, data))

                if data < 0.0:
                    # this happens too often (negative values)
                    self.timestamps.append(np.datetime64(ts))
                    self.data.append(1.0e-11)
                    logging.warning("Unexpected value='{}'".format(line.strip()))
                else:
                    self.timestamps.append(np.datetime64(ts))
                    self.data.append(data)
                    logging.debug(f"#{self.sample_count}:{ts}: {data}")

                if (self.sample_count % 1000 == 0):
                    logging.debug("{}: '{}' -> {}".format(ts.strftime("%H:%M:%S.%f"), line.rstrip(), data))
                    dt = datetime.now() - self.dataStartTS
                    logging.info("Received {} samples in {:.0f}ms ({:.2f} samples/second)".format(self.sample_count, 1000*dt.total_seconds(), self.sample_count/dt.total_seconds()))
                    print("Received {} samples in {:.0f}ms ({:.2f} samples/second)".format(self.sample_count, 1000*dt.total_seconds(), self.sample_count/dt.total_seconds()))

            except KeyboardInterrupt:
                logging.info('Terminated by user')
                break

            except ValueError:
                logging.error("Invalid data format: '{}': {}".format(line, sys.exc_info()))
                error_count += 1
                last_sample = (np.datetime64(datetime.now()) - (self.timestamps[-1] if self.sample_count else np.datetime64(datetime.now())))/np.timedelta64(1, 's')
                if (error_count > 100) and  last_sample > data_timeout_ths:
                    logging.error("Aborting. Error rate is too high {} errors, last valid sample received {} seconds ago".format(error_count, last_sample))
                    self.stream_data = False
                    break
                pass

            except serial.SerialException as e:
                logging.error('Serial read error: {}: {}'.format(e.strerror, sys.exc_info()))
                self.stream_data = False
                break

        self.stream_data = False

        # stop streaming so the device shuts down if in auto mode
        logging.info('Telling CR to stop USB streaming')
        
        try:
            # this will throw if the device has failed.disconnected already
            self.serialConnection.write(b'u')
        except:
            logging.warning('Was not able to clean disconnect from the device')

        logging.info('Serial streaming terminated')

    def textAmp(self, amp):
        if (abs(amp) > 1.0):
            return "{:.3f} A".format(amp)
        if (abs(amp) > 0.001):
            return "{:.2f} mA".format(amp*1000)
        if (abs(amp) > 0.000001):
            return "{:.1f} \u00B5A".format(amp*1000*1000)
        return "{:.1f} nA".format(amp*1000*1000*1000)


    def getSerialData(self, frame, lines, legend, lastText):
        if (self.pause_chart or len(self.data) < 2):
            lastText.set_text('')
            return

        if not self.stream_data:
            self.ax.set_title('<Disconnected>', color="red")
            lastText.set_text('')
            return

        dt = datetime.now() - self.dataStartTS

        # capped at buffer_max_samples
        sample_set_size = len(self.data)

        timestamps = []
        samples = [] #np.arange(chart_max_samples, dtype="float64")

        subsamples = max(1, min(max_supersampling, int(sample_set_size/chart_max_samples)))
        
        # Sub-sampling for longer window views without the redraw perf impact
        for i in range(0, chart_max_samples):
            sample_index = int(sample_set_size*i/chart_max_samples)
            timestamps.append(self.timestamps[sample_index])
            supersample = np.array([self.data[i] for i in range(sample_index, sample_index+subsamples)])
            samples.append(np.median(supersample) if median_filter else np.average(supersample))

        self.ax.set_xlim(timestamps[0], timestamps[-1])

        # some machines max out at 100fps, so this should react in 0.5-5 seconds to actual speed
        sps_samples = min(512, sample_set_size);
        dt_sps = (np.datetime64(datetime.now()) - self.timestamps[-sps_samples])/np.timedelta64(1, 's');

        # if more than 1 second since last sample, automatically set SPS to 0 so we don't have until it slowly decays to 0
        sps = sps_samples/dt_sps if ((np.datetime64(datetime.now()) - self.timestamps[-1])/np.timedelta64(1, 's')) < 1 else 0.0
        lastText.set_text('{:.1f} SPS'.format(sps))
        if sps > 500:
            lastText.set_color("white")
        elif sps > 100:
            lastText.set_color("yellow")
        else:
            lastText.set_color("red")


        logging.debug("Drawing chart: range {}@{} .. {}@{}".format(samples[0], timestamps[0], samples[-1], timestamps[-1]))
        lines.set_data(timestamps, samples)
        self.ax.legend(labels=['Last: {}\nAvg: {}'.format( self.textAmp(samples[-1]), self.textAmp(sum(samples)/len(samples)))])


    def isStreaming(self) -> bool:
        return self.stream_data

    def close(self):
        self.stream_data = False

        if self.thread != None:
            self.thread.join()

        if self.serialConnection != None:
            self.serialConnection.close()

        logging.info("Connection closed.")

def serial_ports():
    """ Lists serial port names

        :raises EnvironmentError:
            On unsupported or unknown platforms
        :returns:
            A list of the serial ports available on the system
    """
    if sys.platform.startswith('win'):
        logger.debug("Found a windows machine")
    elif sys.platform.startswith('linux'):
        logger.debug("Found a linux machine")
    else:
        logger.error("Found a {} machine".format(sys.platform))
        raise EnvironmentError('Unsupported platform')

    result = []
    for port in serial.tools.list_ports.comports():
        logger.debug("""mfg: {}\r
                        VID: {}\r
                        PID: {}\r
                        port: {}
                        """.format(port.manufacturer, port.vid, port.pid, port.device))
        if port.vid != 0x04d8 and port.pid != 0xee4c:
            try:
                s = serial.Serial(port.device)
                s.close()
                result.append(port.device)
            except (OSError, serial.SerialException):
                raise
    return result

def main():

    print("CurrentViewer v" + version)

    log_size = 1024*1024

    log_size = ConfigSectionMap("Configuration")['log_size']

    global refresh_interval
    refresh_interval = ConfigSectionMap("Configuration")['refresh_interval']

    global baud
    baud = ConfigSectionMap("Configuration")['baud']

    max_chart = ConfigSectionMap("Configuration")['max_chart']
    if int(max_chart) and int(max_chart[0]) > 0:
        global chart_max_samples
        chart_max_samples  = int(ConfigSectionMap("Configuration")['chart_max_samples'])


    global buffer_max_samples
    buffer_max_samples = int(ConfigSectionMap("Configuration")['buffer_max_samples'])
    if buffer_max_samples < chart_max_samples:
        print("Command line error: Buffer size cannot be smaller than the chart sample size", file=sys.stderr)
        return -1


    global save_file
    global save_format

    save_format = ConfigSectionMap("Configuration")['format']
    save_format = save_format.upper()
    if not save_format in ["CSV", "JSON"]:
        print(f"Unknown format {save_format}", file=sys.stderr)
        return -2

    output_file_name = easygui.filesavebox(msg=None, title=None, default='', filetypes=None)

    save_file = open(output_file_name, "w+")
    if not save_format:
        save_format = 'CSV' if output_file_name.upper().endswith('.CSV') else 'JSON'
        logging.info(f"Save format automatically set to {save_format} for {save_file}")

    if save_format == 'CSV':
        save_file.write("Timestamp, Amps\n")
    elif save_format == 'JSON':
        save_file.write("{\n\"data\":[\n")

    logging.info("CurrentViewer v{}. System: {}, Platform: {}, Machine: {}, Python: {}".format(version, platform.system(), platform.platform(), platform.machine(), platform.python_version()))

    csp = CRPlot(sample_buffer=buffer_max_samples)
    serialport = serial_ports()[0]
    if csp.serialStart(port=serialport, speed=baud):
        msg = "Do you want to view and log data, or only datalog"
        title = "Please Confirm"
        choice = easygui.buttonbox(msg, title, choices=['View + Log', 'Datalog'])
        if choice == 'View + Log':
            print("Starting live chart...")
            csp.chartSetup(refresh_interval=refresh_interval)
        elif choice == 'Datalog':
            print("Running with no GUI (press Ctrl-C to stop)...")
            try:
                while csp.isStreaming():
                    time.sleep(0.01)
            except KeyboardInterrupt:
                logging.info('Terminated')
                csp.close()

            print("Done.")
    else:
        logger.error("Fatal: Could not connect to USB/BT COM port {}. Check the logs for more information".format(serialport))

    csp.close()

    if save_file:
        if save_format == 'JSON':
            save_file.write("\n]\n}\n")
        save_file.close()

if __name__ == '__main__':
  main()
