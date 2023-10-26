# -*- coding: utf-8 -*-

# NOTES
# Please see "Add/Request a new BMS" https://louisvdw.github.io/dbus-serialbattery/general/supported-bms#add-by-opening-a-pull-request
# in the documentation for a checklist what you have to do, when adding a new BMS

# avoid importing wildcards
from battery import Protection, Battery, Cell
from utils import is_bit_set, read_serial_data, logger
import utils
from struct import unpack_from
from typing import Dict
import minimalmodbus
import ctypes


class FelicitySolarRS485(Battery):
    def __init__(self, port, baud, address):
        super(FelicitySolarRS485, self).__init__(port, baud, address)
        self.type = self.BATTERYTYPE
        self.address = address
        self.cell_count = 16
        for i in range(0,16):
            self.cells.append(Cell(False))
        self.temp_sensors = 4

    BATTERYTYPE = "Felicity"
    LENGTH_CHECK = 4
    LENGTH_POS = 3

    def test_connection(self):
        # call a function that will connect to the battery, send a command and retrieve the result.
        # The result or call should be unique to this BMS. Battery name or version, etc.
        # Return True if success, False for failure
        result = True
        logger.info("Querying version information of felicity BMS:")
        mbdev = minimalmodbus.Instrument(self.port, slaveaddress=self.address, mode="rtu", close_port_after_each_call=True, debug=False)
        mbdev.serial.parity = minimalmodbus.serial.PARITY_NONE
        mbdev.serial.stopbits = minimalmodbus.serial.STOPBITS_ONE
        mbdev.serial.baudrate = self.baud_rate
        mbdev.serial.timeout = 1
        self.mbdev = mbdev

        re = self.mbdev.read_registers(0xF80B, 1, 3) # Force modbus command 3, since specified in the felicity protocol
        if(len(re) > 0):
            self.hardware_version = "Felicity Solar BMS V" + str(re[0])

        logger.info(self.hardware_version)

        return result and self.get_settings() and self.refresh_data()

    def get_settings(self):
        # After successful  connection get_settings will be call to set up the battery.
        # Set the current limits, populate cell count, etc
        # Return True if success, False for failure
        logger.info("Get_Settings() got called")

        self.capacity = (
            float(250)  # if possible replace constant with value read from BMS
        )
        self.max_battery_charge_current = (
            float(120)  # if possible replace constant with value read from BMS
        )
        self.max_battery_discharge_current = (
            float(120)  # if possible replace constant with value read from BMS
        )
        self.max_battery_voltage = utils.MAX_CELL_VOLTAGE * self.cell_count
        self.min_battery_voltage = utils.MIN_CELL_VOLTAGE * self.cell_count


        reLimits = self.mbdev.read_registers(0x131C, 4, 3) # Force modbus command 3, since specified in the felicity protocol
        if(4 == len(reLimits)):
            cvl = float(reLimits[0]) / 100.0 # charge voltage limit: volts = int / 100
            dvl = float(reLimits[1]) / 100.0 # discharge voltage limit: volts = int / 100
            ccl = float(reLimits[2]) / 10.0  # charge current limit: amps = int / 10
            dcl = float(reLimits[3]) / 10.0  # discharge current limit: amps = int / 10
            logger.info(f"cvl = {cvl}, dvl = {dvl}, ccl = {ccl}, dcl = {dcl}")
            # Note that these values can vary dynamically during use, so update them regularly.
            self.max_battery_charge_current = ccl
            self.max_battery_discharge_current = dcl
            self.max_battery_voltage = cvl
            self.min_battery_voltage = dvl
           #logger.info("Todo: update limit readout")

        # provide a unique identifier from the BMS to identify a BMS, if multiple same BMS are connected
        # e.g. the serial number
        # If there is no such value, please leave the line commented. In this case the capacity is used,
        # since it can be changed by small amounts to make a battery unique. On +/- 5 Ah you can identify 11 batteries
        # self.unique_identifier = str()
        return True

    def refresh_data(self):
        # call all functions that will refresh the battery data.
        # This will be called for every iteration (1 second)
        # Return True if success, False for failure
        result = self.read_soc_data()

        return result

    def read_status_data(self):
        return True

    def read_soc_data(self):
        re = self.mbdev.read_registers(0x1302, 10, 3) # Force modbus command 3, since specified in the felicity protocol
        if(10 == len(re)):
            self.voltage = float(re[4])/100.0 # Pack voltage = int value / 100
            current_raw = re[5]  # Pack current = int value / 10 # invert current due to different semantics. negative current for felicity means charging from the BMS perspective
            self.current = -1.0 * float(ctypes.c_int16(current_raw & 0xFFFF).value)/10.0  # Pack current = int value / 10 # invert current due to different semantics. negative current for felicity means charging from the BMS perspective
            self.soc = float(re[9])
            self.temp_mos = float(re[8]) # Set the BMS temperature as the mos temperature (closes approximation BMS = mos) using the setter method
            # todo: status and fault flags (bits of value 1 means true for described condition)
            state = re[0] # Bits meaning: 0 = charge enable, 1 = charge necessary, 2 = discharge allowed, rest: reserved/unknown. bit 7 seems to mean "charging"
            fault = re[2] # Bits meaning: 0..1 = reserved, 2 = cell voltage abnormally high, 3 = cell voltage abnormally low, 4 = charge current abnormally high, 5 = discharge current abnormally high, 6 = BMS temperature abnormally high, 7 = reserved, 8 = cell temperature abnormally high, 9 = cell temperature abnormally low, 10..15 = reserved
            logger.info("Fault state: " + str(fault) + ", battery state: " + str(state))
            #self.protection.voltage_high = Protection.ALARM # testing if we can trigger an alarm
            self.protection.voltage_high = Protection.ALARM if(0x4 & fault) else Protection.OK
            self.protection.voltage_cell_low = Protection.ALARM if(0x08 & fault) else Protection.OK
            self.protection.voltage_low = Protection.ALARM if(0x02 & state) else Protection.OK
            self.protection.soc_low = Protection.ALARM if(self.soc < 20) else Protection.OK
            self.protection.current_over = Protection.ALARM if(0x10 & fault) else Protection.OK
            self.protection.current_under = Protection.ALARM if(0x20 & fault) else Protection.OK
            self.protection.temp_high_internal = Protection.ALARM if(0x40 & fault) else Protection.OK
            self.protection.temp_high_charge = Protection.ALARM if(0x100 & fault) else Protection.OK
            self.protection.temp_high_discharge = Protection.ALARM if(0x100 & fault) else Protection.OK
            self.protection.temp_low_charge = Protection.ALARM if(0x200 & fault) else Protection.OK
            self.protection.temp_low_discharge = Protection.ALARM if(0x200 & fault) else Protection.OK
        else:
            self.voltage = 0
            self.current = 0
            self.soc = 0
            self.temp_mos = 0

        reLimits = self.mbdev.read_registers(0x131C, 4, 3) # Force modbus command 3, since specified in the felicity protocol
        if(4 == len(reLimits)):
            cvl = float(reLimits[0]) / 100.0 # charge voltage limit: volts = int / 100
            dvl = float(reLimits[1]) / 100.0 # discharge voltage limit: volts = int / 100
            ccl = float(reLimits[2]) / 10.0  # charge current limit: amps = int / 10
            dcl = float(reLimits[3]) / 10.0  # discharge current limit: amps = int / 10
            logger.info(f"cvl = {cvl}, dvl = {dvl}, ccl = {ccl}, dcl = {dcl}")
            # Note that these values can vary dynamically during use, so update them regularly.
            self.max_battery_charge_current = ccl
            self.max_battery_discharge_current = dcl
            self.max_battery_voltage = cvl
            self.min_battery_voltage = dvl
           #logger.info("Todo: update limit readout")

        reCells = self.mbdev.read_registers(0x132A, 0x18, 3) # Force modbus command 3, since specified in the felicity protocol
        if(0x18 == len(reCells)):
            logger.info("Len of cells: " + str(len(self.cells)))
            for i in range(0,4):
                celltemp = float(reCells[16+i]) # The i-th sensor around the cell bank directly contains an integer temperature value in celsius.
                self.to_temp(sensor=1+i, value=celltemp) # Use the setter method of the base class to clip temperature values
                logger.info(f"temp{i} = {celltemp}")
            for i in range(0,16):
                cellvolts = float(reCells[0+i])/1000.0 # Cell voltage = modbus int value / 1000
                self.cells[i].voltage = cellvolts # Set the i-th cell voltage directly. There is no setter function in the base class.
        else:
            logger.error("Wrong response length")
            #todo: set cell voltages to NaN when not available, temperatures also

        #todo: handle exceptions for wrong answers
        return True

    def manage_charge_voltage(self) -> None:
        """
        manages the charge voltage by setting self.control_voltage
        :return: None
        """
        if utils.CVCM_ENABLE:
            if utils.LINEAR_LIMITATION_ENABLE:
                self.manage_charge_voltage_linear()
            else:
                self.manage_charge_voltage_step()
        # on CVCM_ENABLE = False apply max voltage
        else:
            self.control_voltage = self.max_battery_voltage
            self.charge_mode = "Keep always max voltage"
