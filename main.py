#!/usr/bin/env python3

# Loosely based off Home Assistant's generic thermostat
# https://github.com/home-assistant/core/tree/dev/homeassistant/components/generic_thermostat
import yaml
import logging
import sys
import time
import gpiod
import platform
from gpiod.line import Direction, Value
from gevent.pywsgi import WSGIServer
from flask import Flask, request, jsonify, Response
from flask_classful import FlaskView, route
import paho.mqtt.client as paho
import json
import gevent
import sdnotify

MODE_HEAT = "HEAT"
MODE_COOL = "COOL"
MODE_OFF = "OFF"

MODE_OPEN = 1
MODE_CLOSED = 0

GPIOCHIP = None

def set_gpio_state(gpio, state):
    if GPIOCHIP:
        line = GPIOCHIP.line_offset_from_id(gpio)
        request = self.gpiochip.request_lines(config={  
            line: gpiod.LineSettings(
                direction=Direction.OUTPUT
            )
        })
        request.set_value(line, Value.ACTIVE if state else Value.INACTIVE)
    else:
        print(f"Setting {gpio} to {state}")

class Display:

    def __init__(self, control_unit, thermostats):
        self._control_unit = control_unit
        self._thermostats = thermostats

        self.lines = ["", "Starting up...", "", ""]

    def update(self):

        self.lines[0] = ""
    
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

class ThermostatAPI(FlaskView):
    def __init__(self, args):
       self._thermostats = args

    def index(self):
        ret = {}
        for thermostat in self._thermostats:
            ret[thermostat.id] = thermostat.to_dict()
        resp = jsonify(ret)
        return add_cors(resp)
    
    @route('/<id>', methods=['GET', 'OPTIONS'])
    def thermostat(self, id):
        ret = {}
        for thermostat in self._thermostats:
            if thermostat.id == id:
                ret = thermostat.to_dict()
        resp = jsonify(ret)
        return add_cors(resp)

    @route('/<id>', methods=['POST'])
    def thermostat_set(self, id):
        ret = {}
        if request.method == 'POST':
            for thermostat in self._thermostats:
                if thermostat.id == id:
                    thermostat.set_target_temp(request.json.get('set'))
                    ret = thermostat.to_dict()
        resp = jsonify(ret)
        return add_cors(resp)

class WebAPI(FlaskView):
    help_message = """
    <h1>API definition</h1>
    
    <pre>
    GET /thermostats/
    GET /status/
    GET /device/
    
    POST /thermostats/<id>/
    {"set":22.5}
    </pre>
    """
    def __init__(self, args):
        self._control_unit = args

    def index(self):
        resp = Response(self.help_message)
        return add_cors(resp)

    def device(self):
        resp = jsonify(self._control_unit.get_mqtt_discovery_message())
        return add_cors(resp)

    def status(self):
        resp = jsonify(self._control_unit.to_dict())
        return add_cors(resp)


class ControlUnit:
    def __init__(self, logger, config):
        self.logger = logger
        self.config = config["control"]

        self.id = self.config["id"]
        self.unique_id = self.id
        self.name = self.config["name"]
        self.state_topic = config["mqtt"]["state_topic"]
        self.discovery_topic = config["mqtt"]["discovery_topic"]

        self.hvac_mode = MODE_OFF
        self.set_mode(MODE_OFF)
        self.mode_last_changed = time.monotonic()

        self.min_cycle_duration = self.config["min_cycle_duration"]
        self.valve_min_cycle_duration = self.config["valve_min_cycle_duration"]

        self.mode_preference = self.config["mode_preference"]

        self.heat_gpio = self.config["heat_relay_gpio"]
        self.cool_gpio = self.config["cool_relay_gpio"]

        self.modes = {}
        self.valves = {}
        self.valve_requests = {}
        self.valve_last_changed = {}
        self.gpios = {}
        self.cooling_enabled = {}
        self.heating_enabled = {}
        for room in config["rooms"]:
            room_id = room["id"]
            self.modes[room_id] = MODE_OFF
            
            self.valves[room_id] = MODE_CLOSED
            self.valve_requests[room_id] = MODE_CLOSED
            self.valve_last_changed[room_id] = time.monotonic()

            self.cooling_enabled[room_id] = True
            self.heating_enabled[room_id] = True
            if "cooling" in room and not room["cooling"]:
                self.cooling_enabled[room_id] = False
            if "heating" in room and not room["heating"]:
                self.heating_enabled[room_id] = False

            self.gpios[room_id] = room["relay_gpio"]

    def mode(self):
        return self.hvac_mode

    def set_mode(self, new_mode):
        if self.hvac_mode != new_mode:
            self.logger.info(f"Control unit switching to {new_mode}")

            # Turn off both relays            
            set_gpio_state(self.heat_gpio, False)
            set_gpio_state(self.cool_gpio, False)

            gevent.sleep(1)

            self.hvac_mode = new_mode
            if self.hvac_mode == MODE_HEAT:
                set_gpio_state(self.heat_gpio, True)
            if self.hvac_mode == MODE_COOL:
                set_gpio_state(self.cool_gpio, True)


    def room_mode(self, room_id):
        return self.modes[room_id]

    def request_mode(self, room_id, new_mode):
        if self.modes[room_id] != new_mode:
            self.logger.info(f"Room {room_id} requests {new_mode}")
            self.modes[room_id] = new_mode

    def operate_valve(self, room_id, new_state):
        if self.hvac_mode == MODE_HEAT and not self.heating_enabled[room_id]:
            new_state = MODE_CLOSED
        if self.hvac_mode == MODE_COOL and not self.cooling_enabled[room_id]:
            new_state = MODE_CLOSED

        self.logger.info(f"Changing valve {room_id} state from {self.valves[room_id]} to {new_state}")
        
        gpio = self.gpios[room_id]
        set_gpio_state(gpio, True if new_state == MODE_OPEN else False)
        
        self.valves[room_id] = new_state
        self.valve_last_changed[room_id] = time.monotonic()

    def to_dict(self):
        return self.get_mqtt_state_message()

    def publish_mqtt_state_message(self, client):
        msg = self.get_mqtt_state_message()
        state_topic = self.state_topic.replace("<component>", "device").replace("<object_id>", self.id)
        logger.info(f"Publishing control unit state message for {self.id} to {state_topic}")
        client.publish(state_topic, json.dumps(msg), qos=2)

    def get_mqtt_state_message(self):
        msg = {
            "heat": (self.hvac_mode == MODE_HEAT),
            "cold": (self.hvac_mode == MODE_COOL),
            "valves": {},
        }
        for room_id, valve in self.valves.items():
            msg["valves"][room_id] = (valve == MODE_OPEN)
        return msg

    def publish_mqtt_discovery_message(self, client):
        msg = self.get_mqtt_discovery_message()
        discovery_topic = self.discovery_topic.replace("<component>", "device").replace("<object_id>", self.id)
        logger.info(f"Publishing control unit discovery message for {self.id} to {discovery_topic}")
        client.publish(discovery_topic, json.dumps(msg), qos=2)

    def get_mqtt_discovery_message(self):
        mqtt_id = self.id
        state_topic = self.state_topic.replace("<component>", "device").replace("<object_id>", mqtt_id)
        msg = { 
            "dev": {
                "ids": mqtt_id,
                "name": self.name,
                "mf": self.config["manufacturer"],
                "mdl": self.config["model"],
                "sw": "1.0",
                "sn": "123",
                "hw": self.config["manufacturer"],
            },
            "o": {
                "name": "pythonthermostat",
                "sw": platform.python_version(),
                "url": "https://github.com/rosmo/zigbee-thermostat-connector",
            },
            "cmps": {
                "heat": {
                    "p": "binary_sensor",
                    "device_class": "heat",
                    "value_template": "{{ value_json.heat }}",
                    "unique_id": f"{self.unique_id}sth",
                },
                "cold": {
                    "p": "binary_sensor",
                    "device_class": "cold",
                    "value_template": "{{ value_json.cold }}",
                    "unique_id": f"{self.unique_id}stc",
                }
            },
            "state_topic": state_topic,
            "qos": 2
        }
        for room_id, valve in self.valves.items():
            msg["cmps"][f"valve_{room_id}"] = {
                "p": "binary_sensor",
                "device_class": "opening",
                "value_template": ("{{ value_json.valves.%s }}" % (room_id)),
                "unique_id": f"{self.unique_id}v{room_id}",
            }
        return msg


    def control(self):
        heating_requested = any(map(lambda x: x == MODE_HEAT, self.modes.values()))
        cooling_requested = any(map(lambda x: x == MODE_COOL, self.modes.values()))

        self.logger.info(f"Heating requested: {heating_requested}, cooling requested: {cooling_requested}")
        new_mode = MODE_OFF
        if heating_requested and cooling_requested:
            if self.mode_preference == MODE_HEAT:
                new_mode = MODE_HEAT
            else:
                new_mode = MODE_COOL
        elif heating_requested:
            new_mode = MODE_HEAT
        elif cooling_requested:
            new_mode = MODE_COOL
        else:
            # Could be heat, cool or off
            new_mode = self.mode_preference
        if (time.monotonic() - self.mode_last_changed) >= self.min_cycle_duration:
            self.set_mode(new_mode)

        for room_id, room_request in self.modes.items():
            if self.hvac_mode == MODE_COOL:
                if room_request == MODE_COOL:
                    self.valve_requests[room_id] = MODE_OPEN
                else:
                    self.valve_requests[room_id] = MODE_CLOSED
            if self.hvac_mode == MODE_HEAT:
                if room_request == MODE_HEAT:
                    self.valve_requests[room_id] = MODE_OPEN
                else:
                    self.valve_requests[room_id] = MODE_CLOSED
            if self.hvac_mode == MODE_OFF:
                self.valve_requests[room_id] = MODE_CLOSED

        for room_id, desired_state in self.valve_requests.items():
            if self.valves[room_id] != desired_state:
                if (time.monotonic() - self.valve_last_changed[room_id]) >= self.valve_min_cycle_duration:
                    self.operate_valve(room_id, desired_state)
            
class Thermostat:
    def __init__(self, logger, control_unit, config, room):
        self.logger = logger
        self.config = config
        self.control_unit = control_unit

        self.id = room["id"]
        self.room = room
        self.zigbee2mqtt = {}
        if "zigbee2mqtt" in room:
            self.zigbee2mqtt = room["zigbee2mqtt"]

        self.max_temp = float(config["control"]["max_temperature"])
        self.min_temp = float(config["control"]["min_temperature"])

        self.target_temp = float(config["control"]["initial_temperature"])
        self.current_temp = float(config["control"]["initial_temperature"])

        self.current_mode = MODE_OFF
        self.cooling_supported = self.room["cooling"] if "cooling" in self.room else True
        self.heating_supported = self.room["heating"] if "heating" in self.room else True

        self.cold_tolerance = float(config["control"]["cold_tolerance"])
        self.heat_tolerance = float(config["control"]["heat_tolerance"])

        self.state_topic = config["mqtt"]["state_topic"]
        self.discovery_topic = config["mqtt"]["discovery_topic"]
        self.component_type = "sensor"
        self.unique_id = f"sensor{self.id}"

    def set_target_temp(self, v):
        if v:
            self.target_temp = float(v)

    def set_current_temp(self, v):
        if v:
            self.current_temp = float(v)

    def set_max_temp(self, v):
        if v:
            self.max_temp = float(v)
    
    def set_min_temp(self, v):
        if v:
            self.min_temp = float(v)

    def get_zigbee2mqtt(self):
        return self.zigbee2mqtt

    def set_target_temperature(self, temperature):
        self.target_temp = float(temperature)
        if self.target_temp > self.max_temp:
            self.target_temp = self.max_temp
        elif self.target_temp < self.min_temp:
            self.target_temp = self.min_temp

    def set_current_temperature(self, temperature):
        self.current_temp = float(temperature)


    def publish_mqtt_state_message(self, client):
        msg = self.get_mqtt_state_message()
        state_topic = self.state_topic.replace("<component>", "device").replace("<object_id>", self.id)
        logger.info(f"Publishing thermostat unit state message for {self.id} to {state_topic}")
        client.publish(state_topic, json.dumps(msg), qos=2)

    def get_mqtt_state_message(self):
        return {
            "temperature": self.current_temp,
            "setpoint_temperature": self.target_temp,
            "heat": (self.current_mode == MODE_HEAT),
            "cool": (self.current_mode == MODE_COOL),
        }

    def to_dict(self):
        ret = self.get_mqtt_state_message()
        ret["name"] = self.room["name"]
        return ret

    def publish_mqtt_discovery_message(self, client):
        msg = self.get_mqtt_discovery_message()
        discovery_topic = self.discovery_topic.replace("<component>", "device").replace("<object_id>", self.id)
        logger.info(f"Publishing thermostat discovery message for {self.id} to {discovery_topic}")
        client.publish(discovery_topic, json.dumps(msg), qos=2)

    def get_mqtt_discovery_message(self):
        mqtt_id = self.id
        state_topic = self.state_topic.replace("<component>", "device").replace("<object_id>", mqtt_id)
        msg = { 
            "dev": {
                "ids": mqtt_id,
                "name": self.room["name"],
                "mf": "Homebrew",
                "mdl": "Python",
                "sw": platform.uname(),
                "sn": "123",
                "hw": platform.machine(),
            },
            "o": {
                "name": "pythonthermostat",
                "sw": platform.python_version(),
                "url": "https://github.com/rosmo/zigbee-thermostat-connector",
            },
            "cmps": {
                "current_temperature": {
                    "p": self.component_type,
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                    "value_template": "{{ value_json.temperature }}",
                    "unique_id": f"{self.unique_id}c",
                },
                "set_temperature": {
                    "p": self.component_type,
                    "device_class": "temperature",
                    "unit_of_measurement": "°C",
                    "value_template": "{{ value_json.setpoint_temperature }}",
                    "unique_id": f"{self.unique_id}sp",
                },
                "heat": {
                    "p": "binary_sensor",
                    "device_class": "heat",
                    "value_template": "{{ value_json.heat }}",
                    "unique_id": f"{self.unique_id}sth",
                },
                "cold": {
                    "p": "binary_sensor",
                    "device_class": "cold",
                    "value_template": "{{ value_json.cold }}",
                    "unique_id": f"{self.unique_id}stc",
                },
            },
            "state_topic": state_topic,
            "qos": 2
        }
        return msg

    def control(self):

        min_temp = self.target_temp - self.cold_tolerance
        max_temp = self.target_temp + self.heat_tolerance

        if self.current_temp < min_temp or self.current_temp > max_temp:
            if self.current_temp > self.target_temp:
                self.control_unit.request_mode(self.id, MODE_COOL)
                self.current_mode = MODE_COOL
            else:
                self.control_unit.request_mode(self.id, MODE_HEAT)
                self.current_mode = MODE_HEAT
        else:
            self.control_unit.request_mode(self.id, MODE_OFF)
            self.current_mode = MODE_OFF

def control_loop(control_unit, thermostats):
    while True:
        for thermostat in thermostats:
            thermostat.control()
        
        control_unit.control()

        gevent.sleep(1)

        n = sdnotify.SystemdNotifier()
        n.notify("WATCHDOG=1")


def on_mqtt_message(client, userdata, msg):
    logger, mqtt_config, control_unit, thermostats = userdata
    try:
        if msg.topic.startswith(mqtt_config["zigbee2mqtt_topic"]):
            topic_s = msg.topic.split("/")
            try:
                payload_decoded = json.loads(msg.payload)
                for thermostat in thermostats:
                    zb2mqtt = thermostat.get_zigbee2mqtt() 
                    if "source" in zb2mqtt:
                        if topic_s[1] == zb2mqtt["source"]:
                            for k, v in zb2mqtt.items():
                                if k != "source" and v in payload_decoded:
                                    getattr(thermostat, f"set_{k}")(payload_decoded[v])
            except Exception as e:
                logger.warning("Malformed payload received from MQTT: %s" % (str(e)))
    except Exception as e:
        logger.error("Error during MQTT message processing: %s" % (str(e)))

def mqtt_loop(logger, mqtt_config, control_unit, thermostats):
    client = paho.Client(paho.CallbackAPIVersion.VERSION2)
    client.user_data_set((logger, mqtt_config, control_unit, thermostats))
    client.on_message = on_mqtt_message
    
    logger.info("Connecting to MQTT server at %s:%d" % (mqtt_config["server"], int(mqtt_config["port"])))
    client.username_pw_set(mqtt_config["username"], mqtt_config["password"])
    client.connect(mqtt_config["server"], int(mqtt_config["port"]), 60)
    logger.info("Subscribing to topic: %s#" % (mqtt_config["zigbee2mqtt_topic"]))
    client.subscribe(f"{mqtt_config['zigbee2mqtt_topic']}#", 0)

    control_unit.publish_mqtt_discovery_message(client)
    for thermostat in thermostats:
        thermostat.publish_mqtt_discovery_message(client)

    loop = 0
    before_loop = time.monotonic()
    client.loop_start()
    while True:
        gevent.sleep(1)
        after_loop = time.monotonic()
        if (after_loop - before_loop) >= 1.0:
            before_loop = after_loop
            loop = loop + 1
            if loop % 3 == 0:
                # publish state messages
                control_unit.publish_mqtt_state_message(client)
                for thermostat in thermostats:
                    thermostat.publish_mqtt_state_message(client)
            if loop == 10:
                # Publish discovery messages
                control_unit.publish_mqtt_discovery_message(client)
                for thermostat in thermostats:
                    thermostat.publish_mqtt_discovery_message(client)
                loop = 0

if __name__ == "__main__":
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.DEBUG)

    config = {}
    with open(sys.argv[1], "r") as f:
        config = yaml.load(f, Loader=yaml.SafeLoader)

    try:
        GPIOCHIP = gpiod.Chip(config["gpio_chip"])
    except Exception as e:
        logger.fatal(f"Failed to open GPIO chip: {config['gpio_chip']}: {e}")
        # sys.exit(1)

    control_unit = ControlUnit(logger, config)
    thermostats = [Thermostat(logger, control_unit, config, room) for room in config["rooms"]]

    app = Flask(__name__)
    WebAPI.register(app, route_base="/", init_argument=(control_unit))
    ThermostatAPI.register(app, route_base="/thermostats", init_argument=(thermostats))

    http_server = WSGIServer(('', 8080), app)
    srv_greenlet = gevent.spawn(http_server.serve_forever)
    mqtt_greenlet = gevent.spawn(mqtt_loop, logger, config["mqtt"], control_unit, thermostats)
    control_greenlet = gevent.spawn(control_loop, control_unit, thermostats)

    try:
        n = sdnotify.SystemdNotifier()
        n.notify("READY=1")
        gevent.joinall([srv_greenlet, control_greenlet, mqtt_greenlet])
    except KeyboardInterrupt:
        http_server.stop()
        logger.warning("Exiting...")
