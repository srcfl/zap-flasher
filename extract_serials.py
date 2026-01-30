#!/usr/bin/env python3
"""Extract unique ecc_serial values from all CSV files."""

import csv
import glob
from collections import defaultdict

def main():
    serial_files = defaultdict(set)
    
    for csv_file in glob.glob("*.csv"):
        with open(csv_file, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "ecc_serial" in row and row["ecc_serial"]:
                    serial_files[row["ecc_serial"]].add(csv_file)
    
    with open("sn.txt", "w") as f:
        for serial in sorted(serial_files.keys()):
            f.write(serial + "\n")
    
    print(f"Extracted {len(serial_files)} unique serials to sn.txt\n")
    
    # Log serials appearing in multiple files
    multi_file = {s: files for s, files in serial_files.items() if len(files) > 1}
    if multi_file:
        print(f"Serials appearing in multiple files ({len(multi_file)}):")
        for serial in sorted(multi_file.keys()):
            print(f"  {serial}:")
            for f in sorted(multi_file[serial]):
                print(f"    - {f}")
    else:
        print("No serials appear in multiple files.")

if __name__ == "__main__":
    main()
