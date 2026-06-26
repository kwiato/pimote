#!/bin/bash
modprobe libcomposite
cd /sys/kernel/config/usb_gadget/ || exit 1
mkdir -p picontroller
cd picontroller

echo 0x1d6b > idVendor   # Linux Foundation
echo 0x0104 > idProduct  # Multifunction Composite Gadget
echo 0x0100 > bcdDevice  # v1.0.0
echo 0x0200 > bcdUSB     # USB 2.0

mkdir -p strings/0x409
echo "fedcba9876543210" > strings/0x409/serialnumber
echo "pimote"           > strings/0x409/manufacturer
echo "Pi Keyboard"      > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "Config 1: Keyboard" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

# --- HID function: keyboard ---
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol      # keyboard
echo 1 > functions/hid.usb0/subclass      # boot interface
echo 8 > functions/hid.usb0/report_length # 8 bytes per report
echo -ne \\x05\\x01\\x09\\x06\\xa1\\x01\\x05\\x07\\x19\\xe0\\x29\\xe7\\x15\\x00\\x25\\x01\\x75\\x01\\x95\\x08\\x81\\x02\\x95\\x01\\x75\\x08\\x81\\x03\\x95\\x05\\x75\\x01\\x05\\x08\\x19\\x01\\x29\\x05\\x91\\x02\\x95\\x01\\x75\\x03\\x91\\x03\\x95\\x06\\x75\\x08\\x15\\x00\\x25\\x65\\x05\\x07\\x19\\x00\\x29\\x65\\x81\\x00\\xc0 > functions/hid.usb0/report_desc

ln -s functions/hid.usb0 configs/c.1/

# bind the gadget to the USB controller
ls /sys/class/udc > UDC

# let the panel write without root
chmod 666 /dev/hidg0