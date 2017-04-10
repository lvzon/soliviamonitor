#!/usr/bin/python3

# soliviamonitor.py

# A python-script for monitoring the status of Delta Solivia RPI PV-inverters
# Tested with Delta Solivia RPI M15A and M20A (European three-phase models)

# Copyright (c) 2016, 2017 Levien van Zon (levien at zonnetjes.net)

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
import datetime
import csv
import os.path
import sys
import signal

import crc16

reporting = True
try:
    import report
    print("Will report energy totals to an external server")
except ImportError:
    reporting = False
    print("Will NOT report energy totals to an external server")

verbose = 1                 # Verbosity flag
debugging = 0               # Debugging flag
inverters = 2               # Number of inverters (TODO: actually use this variable)
basepath = "/root/delta/"   # Path where CSV output files should be saved 

connection = serial.Serial('/dev/ttyUSB0',19200,timeout=0.2);   # Serial device

# Variables in the data-block of a Delta RPI M-series inverter,
# as far as I've been able to establish their meaning.
# The fields for each variable are as follows: 
# name, struct-definition, size in bytes, multiplier-exponent (10^x), unit, SunSpec equivalent

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


structstr = ">"     # Initial struct description string for the data block
structlen = 0       # Length of our struct data
varheader = []      # Variable names for the CSV header

idx = 0
varlookup = {}      # Dict for finding the index of a given variable

# Construct a full struct description for our data block, 
# and a header line for our CSV.

for var in rvars:
    varheader.append(var[0])
    structstr += var[1]
    structlen += var[2]
    varlookup[var[0]] = idx
    idx += 1

if debugging:
    print(varheader)


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

for inv in range(0, inverters):
    samples.append(list())
    total_energy_Wh.append(0) 
    total_energy_Wh_prev.append(0)
    csvwriter_subset.append(0)   
    csvfile = open('/tmp/inv' + str(inv + 1) + '.csv', "a")
    csvwriter_raw.append(csv.writer(csvfile, delimiter='\t'))
    lastsampletime.append(datetime.datetime.now())


def write_samples(use_report):
    
    ''' 
    Write stored samples to CSV-files and optionally report 
    energy totals to an external server 
    '''
    
    global lastlogtime
    
    for inv in range(0, inverters):
        
        # Write our samples to the CSV-file for this inverter
        
        if verbose:
            print("Writing samples to CSV for inverter with index", inv)
        
        try:
        
            for sample in samples[inv]:
                csvwriter_subset[inv].writerow(sample)
                
            samples[inv] = list()   # Clear sample-lists
        
        except:
            
            error = sys.exc_info()[0]
            print(time(), "Error writing samples to file:", str(error))
            
        # Update total energy counters
    
        if total_energy_Wh[inv] and total_energy_Wh[inv] != total_energy_Wh_prev[inv]:
            
            if reporting and use_report:
                try:
                    if verbose:
                        print("Reporting energy total to server, inverter index", inv)
                    report.send_total(inv, total_energy_Wh[inv])
                except:
                    print("Error while calling report.send_total:", str(sys.exc_info()[0]))
                    
            total_energy_Wh_prev[inv] = total_energy_Wh[inv]
        
    lastlogtime = datetime.datetime.now()   # Update last log time


# Catch SIGINT/SIGTERM/SIGKILL and exit gracefully

def signal_handler(signal, frame):
    
    ''' Signal handler to write data when a lethal signal is received '''
    
    print("Received signal:", signal)
    write_samples(False)
    sys.exit(0)

# Register signal handlers

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def send_request (connection, inv_id, cmd):
    
    """ Send command (e.g. b'\x60\x01') to the inverter with id inv_id """
    
    # Borrowed from DeltaPVOutput
    
    length = len(cmd)
    msgbody = struct.pack('BBB%ds'%length, 5, inv_id, length, cmd)
    crcval = crc16.calcData(msgbody)
    lsb = crcval & (0xff)
    msb = (crcval >> 8) & 0xff
    data = struct.pack('BBBB%dsBBB'%length, 2, 5, inv_id, length, cmd, lsb, msb, 3)
    
    if debugging:
        print("Sending data query to inverter", inv_id)
        
    connection.write(data)
    connection.flush()


def get_message (connection, timeout):
    
    """ 
    Attempt to read a Delta-message from a serial connection, 
    with an optional time-out in seconds 
    """
    
    connection.timeout = timeout    # Timeout for serial data read, in seconds
    
    data = connection.read(1)       # Read one byte

    if data and data[0] == 0x02:    # Look for STX

        newdata = connection.read(1)       # STX found, read another byte
        data = bytearray(data)             # Make "bytes" a mutable byte-array, so we can append bytes

        if newdata: 

            if newdata[0] == 0x05 or newdata[0] == 0x06:    # ENQ or ACK
                
                data.extend(newdata)        # Append ACK to STX
                
                newdata = connection.read(2)    # Read 2 more bytes
                
                if newdata:
                    data.extend(newdata)
                    inv_id = newdata[0]            # Inverter ID on the RS485-bus
                    length = newdata[1]            # Response length (including CMD, excluding CRC and ETX)
                else:
                    return None                    # Timeout, discard data
                
                if debugging:
                    print("Found message from/for inverter", inv_id, "with length", length)
                
                newdata = connection.read(length + 3)  # Read 'length' bytes + 2 bytes CRC + 1 byte ETX
                
                if not newdata:                     # If we get a timeout, discard data
                    return None
                
                data.extend(newdata)                # Append data to buffer and return the buffer
                return data
                

            elif debugging:
                print("Received STX 0x02, but invalid ENQ/ACK:", data[0])
                
            return None
                
    elif data and debugging:
        print("Ignoring byte:", data[0])
        
    return None
    

def decode_response (data):

    """ 
    Try to decode an inverter-messages from serial data and return 
    a dictionary with message parameters (including length, inverter_id, 
    command, subcommand).
    Checks message validity, consistency and CRC, and returns None 
    if a message is not valid. Request-messages are parsed, but are 
    currently not returned. 
    """
    
    try:
    
        stx = data[0]
        enqack = data[1]
        
        if stx != 0x02:
            if verbose:
                print("Invalid message, STX =", stx)
            return None
                        
        if enqack != 0x05 and enqack != 0x06:
            if verbose:
                print("Invalid message, ENQ/ACK =", enqack)
            return None
        
        inv_id = data[2]
        length = data[3]
        
        if (len(data)) < length + 4 + 3:    # should be 4 bytes (STX + ACK + ID + LEN) + data length + 3 bytes (CRC16 + ETX) 
            if verbose:
                print("Incomplete data block of", len(data), "bytes, should be", length + 7, "bytes")
            return None
        
        cmd = data[4]                   # Command ID
        subcmd = data[5]                # Subcommand ID
        
        data_offset = 6                 # Start of data
        data_length = length - 2        # Length of data
        
        crc_lsb = data[4 + length]      # Least-significant byte of CRC-16 over preceding bytes after STX
        crc_msb = data[4 + length + 1]  # Most-significant byte of CRC-16 over preceding bytes after STX
        
        etx = data[4 + length + 2]      # ETX-byte to signify end of message, should be 0x03
        
        rvals = {'enqack': enqack, 'inv_id': inv_id, 'length': length, 'cmd': cmd, 'subcmd': subcmd, \
                 'data_offset': data_offset, 'data_length': data_length}
        
        if etx != 0x03:                         # ETX isn't 0x03, data probably isn't valid
            
            if verbose:
                print("ETX at", length + 2, "is", etx, "but should be 3")
                print(rvals)
            
            return None
            
        else:                                   # ETX is 0x03, we probably have a valid data block
            
            crc_calc = crc16.calcData(data[1 : 4 + length])     # Calculate CRC-16 over message, excluding STX
            crc_msg = crc_msb << 8 | crc_lsb                    # Compare with CRC transmitted at end of message
            
            if crc_calc != crc_msg:
                
                print("WARNING: CRC-16 is", hex(crc_calc), " but should be", hex(crc_msg))
                return None
                
            else:
            
                if enqack == 0x05:                 # ENQ, marks start of request message
                    if debugging:
                        print("Found request-message for inverter", inv_id, "with length", length, "and CMD", cmd, "SUB", subcmd)
                    return None                     # Currently we do not return requests, only replies
        
                if debugging:
                    print("Found valid response:", rvals);
                
                return rvals;
            
    except:
        
        print("Error decoding response:", str(sys.exc_info()[0]))
        return None
        

            

while True:     # Main loop

    data = None

    while not data:
        data = get_message(connection, 1.0)   # Try to read a message, with a 1-second time-out
            
    rvals = decode_response(data)       # Process message
        
    time = datetime.datetime.now()      # Current time

    if rvals:
        
        last_data = time                # Update time of last data read
        
        
        inv_id = rvals['inv_id']
        inv_idx = inv_id - 1
        
        cmd = rvals['cmd']
        subcmd = rvals['subcmd']
        
        data_offset = rvals['data_offset']
        data_length = rvals['data_length']
                      
        if debugging:
            print ("Found reply block for inverter ID", inv_id, "command", cmd, "subcommand", subcmd, "data length", data_length)
            
        start = data_offset                         # Start of the actual data

        b = bytes(data[start:start + structlen])    # Get a block of bytes corresponding to the struct 
        if debugging:
            print (time.isoformat(), "Data length:", len(b))
            
        # Look for a reply to command 0x60 subcommand 0x01
        
        if len(b) == structlen and cmd == 0x60 and subcmd == 0x01:
            
            # We have a data block, unpack it and do something with the data
            
            if debugging:
                print("Unpacking data block")
            
            try:
                u = struct.unpack(structstr, b)     # Unpack the struct into a list of variables
                serial = str(u[1], "ascii")         # Get the inverter serial number
                if debugging:
                    print(u)                    
                
            except:
                error = sys.exc_info()[0]
                print(time(), str(error), "while decoding inverter data block")


            # Update total energy count for this inverter
            
            total_energy_Wh[inv_idx] = u[varlookup["energytotal"]] * 1000
            if debugging:
                print("Inverter", serial, "reports", total_energy_Wh[inv_idx], "Wh total energy")
            
                                    
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
                if reporting:
                    if verbose:
                        print("Initial report of energy total to server, inverter index", inv_idx)
                    report.init(inv_idx, serial)
                    report.send_total(inv_idx, total_energy_Wh[inv_idx])
                         
            subset = list(u[12:])           # Get a subset of the data, without serial and version numbers
            subset[25] = hex(subset[25])    # Variable with unknown meaning, store as hex value
            subset[26] = hex(subset[26])    # Variable with unknown meaning, store as hex value

            if debugging:
                print("Subset:", subset)
            
            # Determine if it's time to store a new sample and/or write our data
            
            t_sample = time - lastsampletime[inv_idx]   # Time since last sample stored
            t_log = time - time                         # (Set t_log to zero)
            if lastlogtime and lastlogtime < time:
                t_log = time - lastlogtime              # Time since last data written
                
            if debugging:
                print("Seconds since last sample:", t_sample.seconds)
                print("Next sample due in:", sampleinterval - t_sample.seconds)
                print("Seconds since last write:", t_log.seconds)
                print("Next write due in:", loginterval - t_log.seconds)
                
            if round(t_sample.seconds) >= sampleinterval:
                
                # It's time to store a sample
                
                if verbose:
                    print("Storing sample")
                    print("Seconds since last sample:", t_sample.seconds)
                    print("Next write due in:", loginterval - t_log.seconds)
                    
                samples[inv_idx].append([time.isoformat()] + subset)            # Store sample in list
                csvwriter_raw[inv_idx].writerow([time.isoformat()] + list(u))   # Write all samples directly to temporary file (on RAM-disk)
                lastsampletime[inv_idx] = datetime.datetime.now()               # Update last sample time

            if lastlogtime == 0 or round(t_log.seconds) >= loginterval:
                if verbose:
                    print("Update time of last data write")
                if (lastlogtime):
                    write_samples(True)
                lastlogtime = datetime.datetime.now()   # Update last log time
                
        else:
            
            # Data did not match our struct
            
            if verbose:
                print ("Data did not match struct.")
            
            break
            
    else:
        
        # No replies found in serial data, continue main loop
        
        continue
    
            
    # Check if we should request data from the inverters
                
    t_data = time - last_data
    
    for inv in range(0, inverters):        
        # If we haven't seen any data or reply in a while, send a request
        t_sample = time - lastsampletime[inv]
        if (t_data.seconds >= 1 and t_sample.seconds >= sampleinterval):          
            send_request(connection, inv + 1, b'\x60\x01')   # Send request for a data block (command 96 subcommand 1)
            # TODO: Check if inverters wait until the bus is free before sending data... 
            
