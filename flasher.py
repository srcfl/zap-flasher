#!/usr/bin/env python3
"""
ESP32 Sequential Flashing Tool
Flashes firmware to ESP32 devices one at a time on a single port

Requirements:
    pip install pyserial esptool
    
Usage:
    # Fast mode (default - no erase, less verbose)
    python flasher.py
    
    # Specify binary directory
    python flasher.py --dir c3_1_1_0
    
    # Safer mode with erase (slower but more reliable)
    python flasher.py --erase
    
    # Quiet mode for production
    python flasher.py --quiet
    
    # Specify port manually
    python flasher.py --port /dev/ttyUSB0
    
    # Manual file specification
    python flasher.py --files 0x0:bootloader.bin 0x8000:partition-table.bin 0x10000:firmware.bin
"""

import subprocess
import serial
import serial.tools.list_ports
import time
import json
import argparse
import sys
import csv  # Add csv import
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import re
from datetime import datetime

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class ESP32SequentialFlasher:
    def __init__(self, bin_dir: str = None, flash_files: dict = None, baudrate: int = 115200, quiet: bool = False, timeout: int = 30):
        self.baudrate = baudrate
        self.quiet = quiet
        self.timeout = timeout
        self.results = []
        self.bin_dir_name = None
        
        if flash_files:
            # Manual flash file specification
            self.flash_files = flash_files
            for addr, path in flash_files.items():
                if not Path(path).exists():
                    raise FileNotFoundError(f"Flash file not found: {path}")
        elif bin_dir:
            # Auto-detect from specified binary directory
            self.flash_files = self.detect_flash_files(bin_dir)
            self.bin_dir_name = Path(bin_dir).name
        else:
            # Auto-detect from default locations
            self.flash_files = self.auto_detect_default()

    def auto_detect_default(self) -> Dict[str, str]:
        """Simple default detection: default_fw or current directory"""
        if Path('default_fw').is_dir():
            print("Using default directory: default_fw")
            self.bin_dir_name = 'default_fw'
            return self.detect_flash_files('default_fw')
        
        # Try current directory
        try:
            files = self.detect_flash_files('.')
            self.bin_dir_name = 'current_dir'
            return files
        except FileNotFoundError:
            raise FileNotFoundError("No binaries found in 'default_fw' or current directory.\nUse --dir or --project to specify location.")

    def detect_flash_files(self, bin_dir: str) -> Dict[str, str]:
        """Auto-detect flash files from binary directory"""
        bin_path = Path(bin_dir)
        if not bin_path.exists():
            raise FileNotFoundError(f"Binary directory not found: {bin_dir}")
        
        flash_files = {}
        
        # Helper to find file in dir or subdirs
        def find_file(filename, search_paths=['.']):
            for p in search_paths:
                f = bin_path / p / filename
                if f.exists():
                    return str(f)
            return None

        # 1. Bootloader (0x0)
        # Look in root and bootloader/ subdir (IDF build structure)
        bootloader = find_file('bootloader.bin', ['.', 'bootloader'])
        if bootloader:
            flash_files['0x0'] = bootloader

        # 2. Partition Table (0x8000)
        # Look in root and partition_table/ subdir
        partition_table = find_file('partition-table.bin', ['.', 'partition_table'])
        if partition_table:
            flash_files['0x8000'] = partition_table

        # 3. OTA Data (0xe000) - Optional but good to have
        ota_data = find_file('ota_data_initial.bin', ['.'])
        if ota_data:
            flash_files['0xe000'] = ota_data

        # 4. Main Firmware (0x10000)
        # Try specific names in order of preference
        main_fw_names = ['fw_controller.bin', 'zap-idf.bin', 'firmware.bin', 'app.bin']
        for name in main_fw_names:
            fw = find_file(name, ['.'])
            if fw:
                flash_files['0x10000'] = fw
                break
        
        # Validation
        required_addresses = ['0x0', '0x8000', '0x10000']
        missing = [addr for addr in required_addresses if addr not in flash_files]
        
        if missing:
            # Fallback to legacy detection if strict structure check failed
            # This handles flat directories with different naming conventions
            if not flash_files:
                # ... existing legacy detection logic could go here, but let's keep it simple for now ...
                pass
            
            # If still missing, raise error
            available_files = [str(f.relative_to(bin_path)) for f in bin_path.rglob('*.bin')]
            raise FileNotFoundError(
                f"Missing required flash files in {bin_dir}\n"
                f"Found: {list(flash_files.values())}\n"
                f"Missing addresses: {missing}\n"
                f"Available .bin files: {available_files}"
            )
        
        # Detect chip type
        chip_type = "ESP32-C3" # Default to C3 for this project
        if not self.quiet:
            print(f"Detected flash files for {chip_type} from {bin_path}:")
            for addr, path in flash_files.items():
                print(f"  {addr}: {Path(path).name}")
        
        return flash_files
    
    def find_esp32_port(self) -> Optional[str]:
        """Find the first available ESP32 serial port"""
        try:
            available_ports = list(serial.tools.list_ports.comports())
            print(f"Available ports: {[p.device for p in available_ports]}")
            
            # Priority 1: Look for common ESP32 USB-to-serial chips
            esp32_keywords = [
                'cp210', 'cp2102', 'cp2104',  # Silicon Labs
                'ch340', 'ch341',              # WCH
                'ftdi', 'ft232',               # FTDI
                'silicon labs',                # Silicon Labs full name
                'usb-serial',                  # Generic USB serial
                'uart',                        # UART bridges
                'jtag',                        # ESP32 JTAG
                'debug unit',                  # ESP32 debug unit
                'espressif'                    # Espressif manufacturer
            ]
            
            for port in available_ports:
                port_desc = port.description.lower()
                port_mfg = (port.manufacturer or '').lower()
                
                print(f"  {port.device}: {port.description} (Manufacturer: {port.manufacturer})")
                
                # Check description and manufacturer for ESP32 chips
                if any(keyword in port_desc or keyword in port_mfg for keyword in esp32_keywords):
                    print(f"Auto-detected ESP32 port: {port.device}")
                    return port.device
            
            # Priority 2: Look for USB ports (exclude built-in serial ports)
            usb_ports = []
            for port in available_ports:
                # Skip built-in serial ports like /dev/ttyS0, /dev/ttyAMA0
                if not any(builtin in port.device for builtin in ['/dev/ttyS', '/dev/ttyAMA', 'COM1', 'COM2']):
                    # Prefer USB-style ports
                    if any(usb_pattern in port.device for usb_pattern in ['/dev/ttyUSB', '/dev/ttyACM', 'COM']):
                        usb_ports.append(port)
            
            if usb_ports:
                selected_port = usb_ports[0].device
                print(f"Auto-detected USB port: {selected_port}")
                if len(usb_ports) > 1:
                    print(f"Note: Multiple USB ports found, using first one")
                    print(f"Available USB ports: {[p.device for p in usb_ports]}")
                return selected_port
            
            # Priority 3: If we have any ports at all, show them to help user
            if available_ports:
                print("Could not auto-detect ESP32 port. Available ports:")
                for port in available_ports:
                    print(f"  {port.device}: {port.description}")
                print("Please specify the correct port with --port parameter")
            else:
                print("No serial ports found. Make sure your ESP32 is connected.")
                
        except ImportError:
            print("Warning: pyserial tools not available for port detection")
            print("Please install pyserial: pip install pyserial")
        
        return None
    
    def wait_for_device_connection(self, port: str, timeout: int = 10) -> bool:
        """Wait for a device to be connected to the specified port"""
        print(f"Checking device connection on {port}...")
        
        try:
            # Try to open the port briefly
            with serial.Serial(port, self.baudrate, timeout=1) as ser:
                if ser.is_open:
                    print(f"✓ Device ready on {port}")
                    time.sleep(0.5)  # Brief stabilization
                    return True
        except (serial.SerialException, FileNotFoundError, PermissionError) as e:
            print(f"✗ Cannot connect to {port}: {e}")
            print("Make sure:")
            print("  1. ESP32 device is connected")
            print("  2. No other programs are using the port")
            print("  3. You have permission to access the port")
            return False
        
        return False
    
    def wait_for_device_disconnection(self, port: str, timeout: int = 120):
        """Wait for the device on the specified port to be disconnected."""
        print(f"Flashing complete. Please disconnect the device from port {port}.")
        start_time = time.time()
        while time.time() - start_time < timeout:
            if not self.check_port_ready(port):
                print(f"{Colors.OKGREEN}✓ Device disconnected from {port}. Ready for the next one.{Colors.ENDC}")
                time.sleep(1)  # Brief pause before next cycle
                return True
            time.sleep(0.5)
        print(f"{Colors.FAIL}✗ Timed out waiting for device on {port} to be disconnected.{Colors.ENDC}")
        return False
    
    def erase_flash(self, port: str, chip_type: str = 'auto') -> bool:
        """Completely erase the ESP32 flash memory"""
        try:
            print(f"Erasing flash on {port}...")
            
            # Add a small delay to ensure port is free
            time.sleep(1.5)
            
            cmd = [
                'esptool.py',
                '--port', port,
                '--baud', str(self.baudrate),
                '--chip', chip_type,
                'erase_flash'
            ]
            
            print(f"Running: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                print(f"✓ Flash erased successfully on {port}")
                print("Erase output:", result.stdout[-200:])  # Show last 200 chars
                return True
            else:
                print(f"✗ Flash erase failed on {port}")
                print("STDOUT:", result.stdout)
                print("STDERR:", result.stderr)
                return False
                
        except subprocess.TimeoutExpired:
            print(f"✗ Flash erase timeout on {port}")
            return False
        except Exception as e:
            print(f"✗ Flash erase error on {port}: {e}")
            return False
    
    def flash_firmware(self, port: str, chip_type: str = 'auto', verify: bool = False) -> bool:
        """Flash all required files to ESP32"""
        try:
            print(f"Flashing firmware to {port}...")
            
            # Add a small delay and verify port exists before flashing
            time.sleep(1.5)
            
            # Check if port still exists
            if not Path(port).exists():
                print(f"✗ Port {port} no longer exists. Device may have disconnected.")
                return False
            
            # Build esptool command with all flash files
            cmd = [
                'esptool.py',
                '--port', port,
                '--baud', str(self.baudrate),
                '--chip', chip_type,
                'write_flash',
                '--flash_mode', 'dio',
                '--flash_freq', '80m',
                '--flash_size', 'detect'
            ]
            
            if verify:
                cmd.append('--verify')
            
            # Add each file with its address
            for address, file_path in self.flash_files.items():
                cmd.extend([address, file_path])
                print(f"  {address}: {Path(file_path).name}")
            
            print(f"Running: {' '.join(cmd[:8])} ...")  # Show command without full paths
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode == 0:
                print(f"✓ Firmware flashed successfully on {port}")
                return True
            else:
                print(f"✗ Firmware flash failed on {port}")
                print("STDOUT:", result.stdout[-500:])  # Last 500 chars
                print("STDERR:", result.stderr[-500:])  # Last 500 chars
                return False
                
        except subprocess.TimeoutExpired:
            print(f"✗ Firmware flash timeout on {port}")
            return False
        except Exception as e:
            print(f"✗ Firmware flash error on {port}: {e}")
            return False
    
    def read_serial_output(self, port: str, timeout: int = 30) -> Optional[Dict]:
        """Read serial output and extract device ID and public key"""
        try:
            print(f"Reading serial output from {port}...")
            
            # Open serial port (not using with statement to match working test)
            ser = serial.Serial(port, self.baudrate, timeout=1)
            
            try:
                # Clear any stale data
                ser.reset_input_buffer()
                
                print(f"Resetting device on {port}...")
                
                # Standard ESP32 reset sequence (DTR=0, RTS=1 -> Reset; DTR=0, RTS=0 -> Run)
                ser.setDTR(False)
                ser.setRTS(True)
                time.sleep(0.1)
                ser.setRTS(False)
                time.sleep(1.0)  # Give it a moment to boot
                
                output_lines = []
                start_time = time.time()
                device_id = None
                public_key = None
                firmware_version = None
                boot_success = False
                last_progress_time = start_time
                
                print(f"Listening for serial data (timeout: {timeout}s)...")
                
                while time.time() - start_time < timeout:
                    if ser.in_waiting > 0:
                        line = ser.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            output_lines.append(line)
                            
                            # Only print important lines, not everything
                            should_print = False
                            
                            # Check for successful boot indicators
                            if any(indicator in line.lower() for indicator in ['app_main', 'hello world', 'setup()', 'ready', 'starting', 'boot', 'esp32', 'chip revision']):
                                boot_success = True
                                should_print = True
                            
                            # Check for boot failures
                            if 'invalid header' in line.lower():
                                print(f"⚠️  Boot issue: {line}")
                                should_print = True
                            
                            # Look for serial number (starts with "zap-")
                            if not device_id:
                                serial_patterns = [
                                    r'serial number:\s*(zap-[A-Fa-f0-9]+)',
                                    r'device id:\s*(zap-[A-Fa-f0-9]+)',
                                    r'serial:\s*(zap-[A-Fa-f0-9]+)',
                                    r'(zap-[A-Fa-f0-9]+)'  # Just look for zap- pattern anywhere
                                ]
                                for pattern in serial_patterns:
                                    serial_match = re.search(pattern, line, re.IGNORECASE)
                                    if serial_match:
                                        device_id = serial_match.group(1)
                                        print(f"✓ Found Serial Number: {device_id}")
                                        should_print = True
                                        break
                            
                            # Look for public key (hex values, typically 64 chars for 32 bytes)
                            if not public_key:
                                key_patterns = [
                                    r'public key:\s*([A-Fa-f0-9]{32,})',
                                    r'pubkey:\s*([A-Fa-f0-9]{32,})',
                                    r'key:\s*([A-Fa-f0-9]{32,})',
                                    r'([A-Fa-f0-9]{64})',  # Look for 64-char hex string
                                    r'([A-Fa-f0-9]{32})'   # Look for 32-char hex string
                                ]
                                for pattern in key_patterns:
                                    key_match = re.search(pattern, line, re.IGNORECASE)
                                    if key_match:
                                        public_key = key_match.group(1)
                                        print(f"✓ Found Public Key: {public_key[:16]}...{public_key[-8:]}")
                                        should_print = True
                                        break

                            # Look for firmware version
                            if not firmware_version:
                                # Match "firmware version: X.Y.Z" specifically
                                # The previous regex was catching "coex firmware version: 831ec70" which appears earlier in logs
                                version_match = re.search(r'(?<!coex )firmware version:\s*([A-Za-z0-9._-]+)', line, re.IGNORECASE)
                                if version_match:
                                    firmware_version = version_match.group(1)
                                    print(f"✓ Found Firmware Version: {firmware_version}")
                                    should_print = True
                            
                            # Print the line only if it's important
                            if should_print:
                                print(f"  {line}")
                            
                            # If we have both, we can break early
                            if device_id and public_key and firmware_version:
                                print("✓ Found serial number, public key, and firmware version!")
                                break
                    else:
                        # Show progress less frequently
                        elapsed = time.time() - start_time
                        if time.time() - last_progress_time >= 10:  # Every 10 seconds instead of 5
                            print(f"Still listening... ({elapsed:.0f}s elapsed, {len(output_lines)} lines captured)")
                            last_progress_time = time.time()
                    
                    time.sleep(0.1)
                
                print(f"Serial read completed. Captured {len(output_lines)} lines in {time.time() - start_time:.1f}s")
                
                # Don't show sample output unless there's an error
                if not device_id or not public_key:
                    print("Sample output lines (for debugging):")
                    for i, line in enumerate(output_lines[-5:]):  # Show last 5 lines instead of first 5
                        print(f"  {i+1}: {line}")
                    if len(output_lines) > 5:
                        print(f"  ... (total {len(output_lines)} lines captured)")
                elif len(output_lines) == 0:
                    print("⚠️  No output captured - device may not be booting correctly")
                
                # Final status check
                if not boot_success and 'invalid header' in '\n'.join(output_lines):
                    print("⚠️  Device appears to have boot issues - firmware may not be flashed correctly")
                
                return {
                    'port': port,
                    'device_id': device_id,
                    'public_key': public_key,
                    'firmware_version': firmware_version,
                    'output_lines': output_lines,
                    'boot_success': boot_success,
                    'timestamp': datetime.now().isoformat()
                }
                
            finally:
                # Always close the serial port
                ser.close()
                
        except Exception as e:
            print(f"✗ Serial read error on {port}: {e}")
            import traceback
            print(f"Traceback: {traceback.format_exc()}")
            return None
    
    def check_port_ready(self, port: str) -> bool:
        """Quietly check if a port is connected and ready."""
        try:
            # Check if port exists in the list of comports
            if port not in [p.device for p in serial.tools.list_ports.comports()]:
                return False
            # Try to open it briefly to ensure it's responsive
            with serial.Serial(port, self.baudrate, timeout=0.5):
                pass
            return True
        except (serial.SerialException, FileNotFoundError, PermissionError):
            return False

    def process_device(self, port: str, device_number: int, erase_first: bool = True, chip_type: str = 'auto', verify: bool = False) -> Dict:
        """Process a single device: erase, flash, and read output"""
        print(f"{Colors.WARNING}\n{'='*60}")
        print(f"PROCESSING DEVICE #{device_number}")
        print(f"{'='*60}{Colors.ENDC}")
        
        result = {
            'device_number': device_number,
            'port': port,
            'success': False,
            'serial_number': None,
            'public_key': None,
            'errors': [],
            'warnings': [],
            'timestamp': datetime.now().isoformat()
        }
        
        try:
            # Step 1: Device connection is already confirmed by the main loop.
            
            # Step 2: Erase flash (optional but recommended for clean state)
            if erase_first:
                if not self.erase_flash(port, chip_type):
                    # Don't fail completely if erase fails - continue with flashing
                    warning_msg = 'Flash erase failed, but continuing with flashing (this is normal for fresh devices)'
                    result['warnings'].append(warning_msg)
                    print(f"⚠️  {warning_msg}")
                else:
                    print("✓ Flash erase completed successfully")
            
            # Step 3: Flash firmware (continue regardless of erase result)
            if not self.flash_firmware(port, chip_type, verify):
                result['errors'].append('Firmware flash failed')
                return result
            
            # Step 4: Read serial output
            print("Waiting for device to stabilize after flashing...")
            time.sleep(2)  # Reduce from 5 to 2 seconds
            
            # Check if port is still available
            if not self.check_port_ready(port):
                print(f"⚠️  Port {port} not ready after flashing. Waiting for device to reconnect...")
                # Wait up to 5 seconds for port to be ready again (reduced from 10)
                for i in range(5):
                    time.sleep(1)
                    if self.check_port_ready(port):
                        print(f"✓ Port {port} ready after {i+1} seconds")
                        break
                else:
                    result['errors'].append('Port not available after flashing')
                    return result
            
            serial_data = self.read_serial_output(port, timeout=self.timeout)
            
            if serial_data:
                result['serial_number'] = serial_data['device_id']
                result['public_key'] = serial_data['public_key']
                result['firmware_version'] = serial_data.get('firmware_version')
                result['output_lines'] = serial_data['output_lines']
                
                if result['serial_number'] and result['public_key']:
                    result['success'] = True
                    print(f"{Colors.OKGREEN}✓ Device #{device_number} completed successfully!{Colors.ENDC}")
                    print(f"  Serial: {result['serial_number']}")
                    pub_key_display = f"{result['public_key'][:16]}...{result['public_key'][-8:]}" if len(result['public_key']) > 24 else result['public_key']
                    print(f"  Public Key: {pub_key_display}")
                    if result['warnings']:
                        print(f"  Warnings: {'; '.join(result['warnings'])}")
                else:
                    if serial_data:
                        print(f"⚠️  Serial read completed but missing data:")
                        print(f"    Serial Number: {'✓' if result['serial_number'] else '✗'} {result['serial_number']}")
                        print(f"    Public Key: {'✓' if result['public_key'] else '✗'} {result['public_key']}")
                        print(f"    Lines captured: {len(serial_data.get('output_lines', []))}")
                        if serial_data.get('output_lines'):
                            print(f"    Sample output: {serial_data['output_lines'][:3]}")
                    result['errors'].append('Could not extract serial number or public key from device output')
            else:
                result['errors'].append('Failed to read serial output')
                print("⚠️  Serial read returned None - check device boot process")
            
            # Always show errors if any
            if result['errors']:
                print(f"{Colors.FAIL}✗ Device #{device_number} errors:{Colors.ENDC}")
                for error in result['errors']:
                    print(f"{Colors.FAIL}    - {error}{Colors.ENDC}")
            
        except Exception as e:
            result['errors'].append(f'Unexpected error: {e}')
        
        print(f"{Colors.OKGREEN}{'='*60}")
        print(f"FINISHED PROCESSING DEVICE #{device_number}")
        print(f"{'='*60}{Colors.ENDC}")
        
        return result
    
    def run_sequential_flashing(self, port: str, erase_first: bool = True, chip_type: str = 'auto', verify: bool = False, output_file_base: str = None):
        """Run sequential flashing process"""
        print(f"Starting sequential flashing on port: {port}")
        print("This script will automatically detect device connection and disconnection.")
        print("1. Connect an ESP32 device to the port")
        print("2. Wait for flashing to complete")
        print("3. Disconnect the device and connect the next one")
        print("4. Press Ctrl+C to stop when done")
        print()
        
        device_number = 1
        all_results = []
        
        # Determine output file base name at the start
        if not output_file_base:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file_base = f"flash_results_{timestamp}"
        else:
            # If a base name is provided, append a timestamp to avoid overwriting if run multiple times
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file_base = f"{output_file_base}_{timestamp}"
        
        csv_output_file = f"{output_file_base}.csv"
        print(f"CSV results will be saved to: {csv_output_file}")
        
        firmware_version_found = False
        
        try:
            while True:
                print(f"{Colors.OKCYAN}\n--- Waiting for device #{device_number} on {port} ---{Colors.ENDC}")
                
                # 1. Wait for connection
                while not self.check_port_ready(port):
                    time.sleep(0.5)
                
                print(f"{Colors.OKBLUE}✓ Device #{device_number} detected on {port}. Starting flash process...{Colors.ENDC}")
                time.sleep(1) # Allow port to stabilize before processing

                result = self.process_device(port, device_number, erase_first, chip_type, verify)
                all_results.append(result)
                
                # Check for version update on first success
                if result['success'] and not firmware_version_found and result.get('firmware_version'):
                    version_str = result['firmware_version'].replace('.', '_')
                    # Update output base and csv filename
                    new_output_base = f"{output_file_base}_V_{version_str}"
                    new_csv_file = f"{new_output_base}.csv"
                    
                    print(f"{Colors.OKGREEN}✓ Detected firmware version {result['firmware_version']}. Updating output filename.{Colors.ENDC}")
                    print(f"  Old: {csv_output_file}")
                    print(f"  New: {new_csv_file}")
                    
                    # If the old file exists (e.g. from previous runs or if we wrote headers), rename it
                    if Path(csv_output_file).exists():
                        try:
                            Path(csv_output_file).rename(new_csv_file)
                            print(f"  Renamed existing file to {new_csv_file}")
                        except Exception as e:
                            print(f"  Warning: Could not rename file: {e}")
                    
                    csv_output_file = new_csv_file
                    output_file_base = new_output_base
                    firmware_version_found = True

                if result['success']:
                    self.append_to_csv(result, csv_output_file)
                else:
                    print(f"{Colors.FAIL}\n✗ Device #{device_number} failed. See errors above.{Colors.ENDC}")

                # 2. Wait for disconnection, whether it succeeded or failed
                if not self.wait_for_device_disconnection(port):
                    print("Stopping due to timeout waiting for disconnection.")
                    break # Exit the main loop if timeout occurs

                device_number += 1
                
        except KeyboardInterrupt:
            print(f"\n\nFlashing stopped by user after {len(all_results)} devices.")
        
        # Save final JSON summary and show summary
        if all_results:
            self.save_json_results(all_results, output_file_base=output_file_base)
            self.print_summary(all_results)
        
        return all_results
    
    def append_to_csv(self, result: Dict, csv_file_path: str):
        """Append a single successful result to a CSV file."""
        if not result.get('success'):
            return

        csv_headers = ['ecc_serial', 'mac_eth0', 'mac_wlan0', 'helium_public_key', 'full_public_key']
        
        # Check if file exists to determine if we need to write headers
        file_exists = Path(csv_file_path).is_file()

        try:
            with open(csv_file_path, 'a', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=csv_headers)
                if not file_exists:
                    writer.writeheader()
                
                ecc_serial = result.get('serial_number', '')
                full_public_key = result.get('public_key', '')
                
                writer.writerow({
                    'ecc_serial': ecc_serial,
                    'mac_eth0': '',  # Empty as per requirement
                    'mac_wlan0': '',  # Empty as per requirement
                    'helium_public_key': '',  # Empty as per requirement
                    'full_public_key': full_public_key
                })
            print(f"Result for device #{result.get('device_number')} appended to: {csv_file_path}")
        except IOError as e:
            print(f"Error writing to CSV file {csv_file_path}: {e}")
        except Exception as e:
            print(f"An unexpected error occurred while writing CSV: {e}")

    def save_json_results(self, results: List[Dict], output_file_base: str):
        """Save final results to a JSON file."""
        # Save JSON results
        json_output_file = f"{output_file_base}.json"
        with open(json_output_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nJSON results saved to: {json_output_file}")

    def print_summary(self, results: List[Dict]):
        """Print summary of results"""
        successful = sum(1 for r in results if r['success'])
        total = len(results)
        
        print(f"\n{'='*60}")
        print(f"SEQUENTIAL FLASH OPERATION SUMMARY")
        print(f"{'='*60}")
        print(f"Total devices processed: {total}")
        print(f"Successful: {successful}")
        print(f"Failed: {total - successful}")
        print(f"Success rate: {successful/total*100:.1f}%" if total > 0 else "No devices processed")
        
        if successful > 0:
            print(f"\nSUCCESSFUL DEVICES:")
            for result in results:
                if result['success']:
                    serial_num = result['serial_number']
                    pub_key = result['public_key']
                    key_display = f"{pub_key[:16]}...{pub_key[-8:]}" if len(pub_key) > 24 else pub_key
                    print(f"  Device #{result['device_number']}: {serial_num} | Key: {key_display}")
        
        failed_devices = [r for r in results if not r['success']]
        if failed_devices:
            print(f"\nFAILED DEVICES:")
            for result in failed_devices:
                print(f"  Device #{result['device_number']}: {', '.join(result['errors'])}")


def list_available_ports():
    """List all available serial ports for debugging"""
    try:
        available_ports = list(serial.tools.list_ports.comports())
        
        print("=== AVAILABLE SERIAL PORTS ===")
        if not available_ports:
            print("No serial ports found!")
            return
            
        for i, port in enumerate(available_ports, 1):
            print(f"{i}. {port.device}")
            print(f"   Description: {port.description}")
            print(f"   Manufacturer: {port.manufacturer}")
            print(f"   VID:PID: {port.vid}:{port.pid}" if port.vid and port.pid else "   VID:PID: Unknown")
            print()
            
    except ImportError:
        print("Error: pyserial not installed. Run: pip install pyserial")


def debug_flash_setup(bin_dir: str = None):
    """Debug function to check flash setup"""
    print("=== FLASH SETUP DEBUG ===")
    
    try:
        flasher = ESP32SequentialFlasher(bin_dir=bin_dir)
        
        print(f"Flash files detected: {len(flasher.flash_files)}")
        for addr, path in flasher.flash_files.items():
            file_path = Path(path)
            size = file_path.stat().st_size if file_path.exists() else 0
            print(f"  {addr}: {file_path.name} ({size} bytes)")
            if not file_path.exists():
                print(f"    ❌ FILE NOT FOUND: {path}")
            elif size == 0:
                print(f"    ❌ FILE IS EMPTY")
            else:
                print(f"    ✅ File OK")
        
        print("\nTesting esptool availability:")
        try:
            result = subprocess.run(['esptool.py', '--help'], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                print("✅ esptool.py is available")
            else:
                print("❌ esptool.py not working properly")
        except FileNotFoundError:
            print("❌ esptool.py not found - install with: pip install esptool")
        except subprocess.TimeoutExpired:
            print("❌ esptool.py timeout")
            
    except Exception as e:
        print(f"❌ Error setting up flasher: {e}")


def test_device_connection(port: str):
    """Test basic device connection"""
    print(f"=== TESTING CONNECTION TO {port} ===")
    
    try:
        # Test 1: Basic chip detection
        print("1. Testing chip detection...")
        cmd = ['esptool.py', '--port', port, 'chip_id']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            print("✅ Chip detection successful")
            print("Output:", result.stdout[-300:])
        else:
            print("❌ Chip detection failed")
            print("STDERR:", result.stderr)
            return False
            
        # Test 2: Flash info
        print("\n2. Reading flash info...")
        cmd = ['esptool.py', '--port', port, 'flash_id']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            print("✅ Flash info read successful")
            print("Output:", result.stdout[-300:])
        else:
            print("❌ Flash info failed")
            print("STDERR:", result.stderr)
            
        return True
        
    except subprocess.TimeoutExpired:
        print("❌ Connection test timeout")
        return False
    except Exception as e:
        print(f"❌ Connection test error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='ESP32 Sequential Flashing Tool')
    
    # Clean arguments
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--dir', help='Directory containing flash files')
    group.add_argument('--project', help='Project name (looks in ../[name]/build)')
    group.add_argument('--files', nargs='+', help='Manual flash files in format addr:file')
    
    parser.add_argument('--port', help='Serial port (auto-detect if not specified)')
    parser.add_argument('--verify-flash', action='store_true', help='Verify flash after writing')
    parser.add_argument('--chip', help='Specify chip type (esp32, esp32c3, esp32s2, esp32s3)')
    parser.add_argument('--list-ports', action='store_true', help='List available serial ports and exit')
    parser.add_argument('--debug', action='store_true', help='Debug mode - check setup without flashing')
    parser.add_argument('--test-connection', action='store_true', help='Test device connection only')
    parser.add_argument('--erase', action='store_true', help='Erase flash before writing (slower but safer)')
    parser.add_argument('--quiet', '-q', action='store_true', help='Quiet mode - less verbose output')
    parser.add_argument('--baudrate', type=int, default=460800, help='Serial baudrate')
    parser.add_argument('--timeout', type=int, default=30, help='Serial read timeout in seconds')
    parser.add_argument('--output-base', help='Base name for output files (e.g., my_flash_run). A timestamp will be appended.')
    
    args = parser.parse_args()
    
    # Handle debug modes
    if args.debug:
        debug_flash_setup(args.dir)
        sys.exit(0)
        
    if args.test_connection:
        if not args.port:
            print("Error: --port required for connection test")
            sys.exit(1)
        success = test_device_connection(args.port)
        sys.exit(0 if success else 1)
    
    # Handle port listing
    if args.list_ports:
        list_available_ports()
        sys.exit(0)
    
    try:
        # Parse flash files
        project_label = None

        if args.files:
            # Parse manual files format: addr:file
            flash_files = {}
            for file_spec in args.files:
                if ':' not in file_spec:
                    print(f"Error: Invalid file format '{file_spec}'. Use addr:file format (e.g., 0x0:bootloader.bin)")
                    sys.exit(1)
                addr, filepath = file_spec.split(':', 1)
                flash_files[addr] = filepath
            
            flasher = ESP32SequentialFlasher(flash_files=flash_files, baudrate=args.baudrate, quiet=args.quiet, timeout=args.timeout)
        else:
            # Directory based
            bin_dir = None
            if args.dir:
                bin_dir = args.dir
                print(f"Targeting directory: {bin_dir}")
                project_label = Path(bin_dir).name
            elif args.project:
                bin_dir = f"../{args.project}/build"
                print(f"Targeting project: {bin_dir}")
                project_label = args.project
            
            flasher = ESP32SequentialFlasher(bin_dir=bin_dir, baudrate=args.baudrate, quiet=args.quiet, timeout=args.timeout)
        
        # Get port
        if args.port:
            port = args.port
            print(f"Using specified port: {port}")
        else:
            port = flasher.find_esp32_port()
            if not port:
                print("No port detected. Please specify port manually with --port")
                sys.exit(1)
        
        # Run sequential flashing
        chip_type = args.chip or 'auto'
        erase_first = args.erase  # Now erase is opt-in instead of opt-out
        
        output_base = args.output_base
        if not output_base:
            # Determine base name from project/dir
            if project_label:
                base_name = project_label
            elif hasattr(flasher, 'bin_dir_name') and flasher.bin_dir_name:
                base_name = flasher.bin_dir_name
            else:
                base_name = None
            
            if base_name and base_name != '.':
                # Clean filename
                clean_label = re.sub(r'[^\w\-]', '_', base_name)
                output_base = f"{clean_label}_flash_results"

        flasher.run_sequential_flashing(port, erase_first, chip_type, args.verify_flash, output_base)
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()