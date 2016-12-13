#!/usr/bin/python3

# soliviamonitor.py

# A python-script for monitoring the status of Delta Solivia RPI PV-inverters
# Tested with Delta Solivia RPI M15A and M20A (European three-phase models)

# Copyright (c) 2016 Levien van Zon (levien at zonnetjes.net)

# MIT License
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import struct
import serial
import datetime, time
import csv
import os.path
import sys
import crc


verbose = 1                 # Verbosity flag
inverters = 2               # Number of inverters (TODO: actually use this variable)
basepath = "/root/delta/"   # Path where CSV output files should be saved 

connection = serial.Serial('/dev/ttyUSB0',19200,timeout=0.2);   # Serial device

# Variables in the data-block of a Delta RPI M-series inverter,
# as far as I've been able to establish their meaning.

rvars = (("partno", "11s", 11),
        ("serial", "18s", 18),
        ("", "6s", 6),
        ("fwrev_pwr_maj", "B", 1),
        ("fwrev_pwr_min", "B", 1),
        ("", "2s", 2),
        ("fwrev_sts_maj", "B", 1),
        ("fwrev_sts_min", "B", 1),
        ("", "2s", 2),
        ("fwrev_disp_maj", "B", 1),
        ("fwrev_disp_min", "B", 1),
        ("", "2s", 2),
        ("ac1V", "H", 2, -1, "V"),
        ("ac1I", "H", 2, -2, "A", "AphA"),
        ("ac1P", "H", 2, 0, "W"),
        ("ac1F1", "H", 2, -2, "Hz"),
        ("ac1V2", "H", 2, -1, "V"),
        ("ac1F2", "H", 2, -2, "Hz"),
        ("ac2V", "H", 2, -1, "V"),
        ("ac2I", "H", 2, -2, "A", "AphB"),
        ("ac2P", "H", 2, 0, "W"),
        ("ac2F1", "H", 2, -2, "Hz"),
        ("ac2V2", "H", 2, -1, "V"),
        ("ac2F2", "H", 2, -2, "Hz"),
        ("ac3V", "H", 2, -1, "V"),
        ("ac3I", "H", 2, -2, "A", "AphC"),
        ("ac3P", "H", 2, 0, "W"),
        ("ac3F1", "H", 2, -2, "Hz"),
        ("ac3V2", "H", 2, -1, "V"),
        ("ac3F2", "H", 2, -2, "Hz"),
        ("dc1V", "H", 2, -1, "V"),
        ("dc1I", "H", 2, -2, "A"),
        ("dc1P", "H", 2, 0, "W"),
        ("dc2V", "H", 2, -1, "V"),
        ("dc2I", "H", 2, -2, "A"),
        ("dc2P", "H", 2, 0, "W"),
        ("power", "H", 2, 0, "W"),
        ("", "H", 2),
        ("", "H", 2),
        ("energytotal_day", "I", 4, 0, "Wh"),
        ("feedintime_day", "I", 4, 0, "s"),
        ("energytotal", "I", 4, 0, "kWh"),
        ("", "I", 4),
        ("temp", "H", 2, 0, "C", "TmpSnk"))


structstr = ">"     # Struct description string for the data block
structlen = 0       # Length of our struct data
varheader = []      # Variable names for the CSV header

idx = 0
varlookup = {}      # Dict for finding the index of a given variable

# Construct a struct description for our data block, 
# and a header line for our CSV.

for var in rvars:
    varheader.append(var[0])
    structstr += var[1]
    structlen += var[2]
    varlookup[var[0]] = idx
    idx += 1

if verbose:
    print (varheader)


# Housekeeping variables for sampling and buffering of data

lastlogtime = 0         # Time of last data write
sampleinterval = 60     # Inverter sampling interval in seconds 
loginterval = 60*10     # Data write-interval in seconds 

idx = 0
data = bytes()

time = datetime.datetime.now()      # Current time
last_data = time - time             # Time of last reply-block (set to zero)

csvwriter_raw = []          # CSV output for "raw" inverter data (written to RAM-disk, not really needed except for debugging)
csvwriter_subset = []       # CSV output for processed inverter data subset
samples = []                # Data-samples stored in memory, to reduce flash-writes
total_energy_Wh = []        # Total energy counter for each inverter 
total_energy_Wh_prev = []   # Previously reported energy count, useful for reporting energy to a server
lastsampletime = []         # Time of last inverter read

# Build lists

for inv in range(0,inverters):
    samples.append(list())
    total_energy_Wh.append(0) 
    total_energy_Wh_prev.append(0)
    csvwriter_subset.append(0)   
    csvfile = open('/tmp/inv' + string(inv + 1) + '.csv', "a")
    csvwriter_raw.append(csv.writer(csvfile, delimiter='\t'))
    lastsampletime.append(datetime.datetime.now())


def send_request (inv_id, cmd):
    
    """ Send command (e.g. '\x60\x01') to the inverter with id inv_id """
    
    # Borrowed from DeltaPVOutput, TODO: Make the code more readable, and test it!
    
    l = len(cmd)
    crc = crc.CRC16.calcString(struct.pack('BBB%ds'%l, 5, inv_id, l, cmd))
    lo = crc & (0xff)
    high = (crc >> 8) & 0xff
    data = struct.pack('BBBB%dsBBB' %len(cmd), 2, 5, inv_id, len(cmd), cmd, lo, high, 3)
    
    if verbose:
        print("Sending data query to inverter", inv_id)
        
    connection.write(data)
    connection.flush()


while True:     # Main loop (TODO: allow a clean exit of the program, without data loss)

    connection.timeout = 1.0    # Timeout for serial data read, in seconds
    if data:
        data = data[idx:] + connection.read(1000)   # Try to read more data
    else:
        data = connection.read(1000)    # Try to read data
        
    time = datetime.datetime.now()      # Current time
    idx = 0
    offset = 0

    if data:
        last_data = time                # Update time of last data read

    while offset >= 0:                  # Look for inverter data blocks in our serial data
        
        offset = data.find(b'\x60\x01', idx)        # Look for a reply to command 0x60 subcommand 0x01
                                                    # TODO: we should actually look for STX (0x02 0x06), and then match command 0x60 0x01
        
        if (offset >= 0):                           # Found a reply
            
            inv_id = data[offset - 2]               # Inverter ID on the RS485-bus
            inv_idx = inv_id - 1
            if verbose:
                print ("Found reply block for inverter ID", inv_id)
                
            start = offset + 2                      # Start of the actual data (TODO: also check the CRC and the data length)

            b = bytes(data[start:start + structlen])    # Get a block of bytes corresponding to the struct 
            if verbose:
                print (time.isoformat(), "Data length:", len(b))
                
            if len(b) == structlen:
                
                # We have a data block, unpack it and do something with the data
                
                u = struct.unpack(structstr, b)     # Unpack the struct into a list of variables
                if verbose:
                    print(u)
                
                serial = str(u[1], "ascii")         # Get the inverter serial number
                                
                csvw = csvwriter_subset[inv_idx]    # Get output file object
                
                if not csvw:                        
                    # Open a CSV-file for this serial, if not already done
                    fname = basepath + str(inv_id) + "-" + serial + ".csv"
                    print("Will write to" + fname)
                    write_header = True
                    if os.path.isfile(fname):
                        write_header = False        # Don't write header if file exists
                    ofile = open(fname, "a")        # Append data
                    csvw = csv.writer(ofile, delimiter='\t')
                    csvwriter_subset[inv_idx] = csvw
                    if write_header:
                        csvw.writerow(["time"] + varheader[12:])    # Write header line
                             
                subset = list(u[12:])           # Get a subset of the data, without serial and version numbers
                subset[25] = hex(subset[25])    # Variable with unknown meaning, store as hex value
                subset[26] = hex(subset[26])    # Variable with unknown meaning, store as hex value

                if verbose:
                    print("Subset:", subset)
                
                # Update total energy count for this inverter
                
                total_energy_Wh[inv_idx] = u[varlookup["energytotal"]] * 1000
                if verbose:
                    print("Inverter", serial, "reports", total_energy_Wh[inv_idx], "Wh total energy")
                
                # Determine if it's time to store a new sample and/or write our data
                
                t_sample = time - lastsampletime[inv_idx]   # Time since last sample stored
                t_log = time - time                         # (Set t_log to zero)
                if lastlogtime and lastlogtime < time:
                    t_log = time - lastlogtime              # Time since last data written
                    
                if verbose:
                    print("Seconds since last sample:", t_sample.seconds)
                    print("Next sample due in:", sampleinterval - t_sample.seconds)
                    print("Seconds since last write:", t_log.seconds)
                    print("Next write due in:", loginterval - t_log.seconds)
                    
                if round(t_sample.seconds) >= sampleinterval:
                    
                    # It's time to store a sample
                    
                    samples[inv_idx].append([time.isoformat()] + subset)            # Store sample in list
                    csvwriter_raw[inv_idx].writerow([time.isoformat()] + list(u))   # Write all samples directly to temporary file (on RAM-disk)
                    lastsampletime[inv_idx] = datetime.datetime.now()               # Update last sample time

                if lastlogtime == 0 or round(t_log.seconds) >= loginterval:
                    
                    for inv in range(0,inverters):
                        
                        # Write our data
                        
                        for sample in samples[inv]:
                            csvwriter_subset[inv].writerow(sample)
                            
                        samples[inv] = list()   # Clear sample-lists
                        
                        # Update total energy counters
                    
                        if total_energy_Wh[inv] and total_energy_Wh[inv] != total_energy_Wh_prev[inv]:
                            total_energy_Wh_prev[inv] = total_energy_Wh[inv]
                        
                    lastlogtime = datetime.datetime.now()   # Update last log time
                                      
                idx += offset + 1   # Advance index into the data we read from serial
                
            else:
                
                # Data did not match our struct
                
                if verbose:
                    print ("Data did not match struct.")
                
                break

               
    # Check if we should request data from the inverters
                
    t_data = time - last_data
    
    for inv in sequence(inverters):        
        # If we haven't seen any data or reply in a while, send a request
        t_sample = time - lastsampletime[inv]
        if (t_data.seconds >= 1 and t_sample.seconds >= sampleinterval):          
            send_request(inv + 1, '\x60\x01')   # Send request for a data block (command 96 subcommand 1)
            # TODO: Check if inverters wait until the bus is free before sending data... 
            
