import importlib.util
import os


class PlotterStatusService:
    def __init__(self, ad, sem):
        """Store shared plotter dependencies and initialize cached status state."""
        self.ad = ad
        self.sem = sem
        self.device_cache = {}
        self.last_usb_id = None
        self.last_known_status = {
            "status": "off",
            "machine": "none",
            "device_info": "none",
            "model_number": None,
            "config": {},
        }

    def get_default_model_number(self):
        """Return the configured fallback model number."""
        return int(os.environ.get("AXIDRAW_MODEL", "4"))

    def get_plotter_name(self):
        """Return the best known plotter display name from cached status data."""
        if self.last_usb_id and self.last_usb_id in self.device_cache:
            machine_name = self.device_cache[self.last_usb_id].get("machine", "Unknown")
            if machine_name and machine_name.lower() != "none":
                return machine_name

        if self.last_usb_id:
            machine_name, _ = self.identify_machine(self.last_usb_id)
            if machine_name and machine_name.lower() != "none":
                return machine_name

        machine_name = self.last_known_status.get("machine", "Unknown")
        if not machine_name or str(machine_name).lower() == "none":
            return "Unknown"

        return machine_name

    def identify_machine(self, device_identifier):
        """Infer machine label and model number from an AxiDraw device identifier."""
        if "/dev/" in device_identifier or "COM" in device_identifier:
            machine_type = "AxiDraw (No nickname assigned)"
            machine_model = self.get_default_model_number()
            print(f"  Device uses port path: {device_identifier}")
            print(f"  Using default model: {machine_model}")
            print("  To identify machine type, assign a nickname using:")
            print("  axicli -m manual -M write_nameYourNicknameHere")
            return machine_type, machine_model

        nickname = device_identifier.lower()

        if "mini" in nickname or "mk" in nickname:
            machine_type = "MiniKit-v2"
            machine_model = 4
        elif "a3" in nickname or "se" in nickname or "large" in nickname:
            machine_type = "AxiDraw-A3/SE"
            machine_model = 2
        elif "xlx" in nickname:
            machine_type = "AxiDraw-XLX"
            machine_model = 3
        elif "v3" in nickname or "v2" in nickname:
            machine_type = "AxiDraw-V2/V3"
            machine_model = 1
        elif "a1" in nickname:
            machine_type = "AxiDraw-SE/A1"
            machine_model = 5
        elif "a2" in nickname:
            machine_type = "AxiDraw-SE/A2"
            machine_model = 6
        else:
            machine_type = f"AxiDraw ({device_identifier})"
            machine_model = self.get_default_model_number()

        print(f"  Device nickname: {device_identifier}")
        print(f"  Detected machine type: {machine_type}")
        print(f"  Model number: {machine_model}")
        return machine_type, machine_model

    def detect_connected_model_number(self):
        """Query the connected AxiDraw name list and infer the active model number."""
        self.ad.plot_setup()
        self.ad.options.mode = "manual"
        self.ad.options.manual_cmd = "list_names"
        self.ad.plot_run()
        axidraw_list = self.ad.name_list

        print(f"Debug - axidraw_list type: {type(axidraw_list)}")
        print(f"Debug - axidraw_list: {axidraw_list}")

        if axidraw_list is not None and len(axidraw_list) > 0:
            device_identifier = axidraw_list[0]
            print(f"Debug - device_identifier: '{device_identifier}'")
            self.last_usb_id = device_identifier
            machine_type, machine_model = self.identify_machine(device_identifier)
            self.last_known_status["machine"] = machine_type
            self.last_known_status["device_info"] = device_identifier
            self.last_known_status["model_number"] = machine_model
            return machine_model

        return self.get_default_model_number()

    def load_axidraw_config(self, config_path):
        """Load a model-specific AxiDraw config module into a serializable dictionary."""
        if not config_path or not os.path.exists(config_path):
            return {}

        try:
            spec = importlib.util.spec_from_file_location("axidraw_config", config_path)
            config_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(config_module)

            config_data = {}

            if hasattr(config_module, 'speed_pendown'):
                config_data['speed_pendown'] = config_module.speed_pendown
            if hasattr(config_module, 'speed_penup'):
                config_data['speed_penup'] = config_module.speed_penup
            if hasattr(config_module, 'accel'):
                config_data['accel'] = config_module.accel

            if hasattr(config_module, 'pen_pos_up'):
                config_data['pen_pos_up'] = config_module.pen_pos_up
            if hasattr(config_module, 'pen_pos_down'):
                config_data['pen_pos_down'] = config_module.pen_pos_down

            if hasattr(config_module, 'pen_rate_raise'):
                config_data['pen_rate_raise'] = config_module.pen_rate_raise
            if hasattr(config_module, 'pen_rate_lower'):
                config_data['pen_rate_lower'] = config_module.pen_rate_lower

            if hasattr(config_module, 'model'):
                config_data['model'] = config_module.model
            if hasattr(config_module, 'const_speed'):
                config_data['const_speed'] = config_module.const_speed
            if hasattr(config_module, 'auto_rotate'):
                config_data['auto_rotate'] = config_module.auto_rotate
            if hasattr(config_module, 'reordering'):
                config_data['reordering'] = config_module.reordering

            if hasattr(config_module, 'pen_delay_down'):
                config_data['pen_delay_down'] = config_module.pen_delay_down
            if hasattr(config_module, 'pen_delay_up'):
                config_data['pen_delay_up'] = config_module.pen_delay_up
            if hasattr(config_module, 'resolution'):
                config_data['resolution'] = config_module.resolution

            travel_dimensions = {}
            if hasattr(config_module, 'x_travel_default'):
                travel_dimensions['x_travel_default'] = config_module.x_travel_default
            if hasattr(config_module, 'y_travel_default'):
                travel_dimensions['y_travel_default'] = config_module.y_travel_default
            if hasattr(config_module, 'x_travel_V3A3'):
                travel_dimensions['x_travel_V3A3'] = config_module.x_travel_V3A3
            if hasattr(config_module, 'y_travel_V3A3'):
                travel_dimensions['y_travel_V3A3'] = config_module.y_travel_V3A3
            if hasattr(config_module, 'x_travel_V3XLX'):
                travel_dimensions['x_travel_V3XLX'] = config_module.x_travel_V3XLX
            if hasattr(config_module, 'y_travel_V3XLX'):
                travel_dimensions['y_travel_V3XLX'] = config_module.y_travel_V3XLX
            if hasattr(config_module, 'x_travel_MiniKit'):
                travel_dimensions['x_travel_MiniKit'] = config_module.x_travel_MiniKit
            if hasattr(config_module, 'y_travel_MiniKit'):
                travel_dimensions['y_travel_MiniKit'] = config_module.y_travel_MiniKit
            if hasattr(config_module, 'x_travel_SEA1'):
                travel_dimensions['x_travel_SEA1'] = config_module.x_travel_SEA1
            if hasattr(config_module, 'y_travel_SEA1'):
                travel_dimensions['y_travel_SEA1'] = config_module.y_travel_SEA1
            if hasattr(config_module, 'x_travel_SEA2'):
                travel_dimensions['x_travel_SEA2'] = config_module.x_travel_SEA2
            if hasattr(config_module, 'y_travel_SEA2'):
                travel_dimensions['y_travel_SEA2'] = config_module.y_travel_SEA2

            config_data['travel_dimensions'] = travel_dimensions
            return config_data

        except Exception as error:
            print(f"Error loading config from {config_path}: {error}")
            return {}

    def get_plotter_status(self):
        """Inspect the connected AxiDraw and return the latest machine status snapshot."""
        status_data = {
            "status": "off",
            "machine": "none",
            "device_info": "none",
            "model_number": None,
            "config": {},
        }

        if not self.sem.acquire(blocking=False):
            status_data = self.last_known_status.copy()
            status_data["status"] = "busy"
            return status_data

        try:
            if self.last_usb_id and self.last_usb_id in self.device_cache:
                cached = self.device_cache[self.last_usb_id]
                status_data = cached.copy()
                status_data["status"] = cached.get("status", "on")
                return status_data

            self.ad.plot_setup()
            self.ad.options.mode = "manual"
            self.ad.options.manual_cmd = "list_names"
            self.ad.plot_run()
            axidraw_list = self.ad.name_list

            print(f"Debug - axidraw_list type: {type(axidraw_list)}")
            print(f"Debug - axidraw_list: {axidraw_list}")

            if axidraw_list is not None and len(axidraw_list) > 0:
                device_identifier = axidraw_list[0]
                print(f"Debug - device_identifier: '{device_identifier}'")
                status_data["device_info"] = device_identifier
                self.last_usb_id = device_identifier

                machine_type, machine_model = self.identify_machine(device_identifier)

                status_data["machine"] = machine_type
                status_data["model_number"] = machine_model

                config_env_key = f"AXIDRAW_MODEL_{machine_model}_CONFIG"
                config_path = os.environ.get(config_env_key)

                if config_path:
                    print(f"  Loading config from: {config_path}")
                    config_data = self.load_axidraw_config(config_path)

                    if machine_model == 1:
                        config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_default')
                        config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_default')
                    elif machine_model == 2:
                        config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_V3A3')
                        config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_V3A3')
                    elif machine_model == 3:
                        config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_V3XLX')
                        config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_V3XLX')
                    elif machine_model == 4:
                        config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_MiniKit')
                        config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_MiniKit')
                    elif machine_model == 5:
                        config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_SEA1')
                        config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_SEA1')
                    elif machine_model == 6:
                        config_data['x_travel'] = config_data.get('travel_dimensions', {}).get('x_travel_SEA2')
                        config_data['y_travel'] = config_data.get('travel_dimensions', {}).get('y_travel_SEA2')

                    if 'travel_dimensions' in config_data:
                        del config_data['travel_dimensions']

                    status_data["config"] = config_data
                    status_data["config"]["config_file"] = config_path
                else:
                    print(f"  No config file found for model {machine_model} (env var: {config_env_key})")
                    status_data["config"]["config_file"] = None

                self.ad.interactive()
                if self.ad.connect():
                    try:
                        raw_string = self.ad.usb_query('QC\r')
                        if isinstance(raw_string, bytes):
                            raw_string = raw_string.decode("utf-8", errors="ignore")
                        if not raw_string:
                            raise ValueError("No QC response from plotter")

                        split_string = raw_string.split(",", 1)
                        voltage_value = int(split_string[1])
                        if voltage_value >= 250:
                            status_data["status"] = "on"
                        else:
                            status_data["status"] = "connected"

                        status_data["voltage"] = voltage_value
                    except (ValueError, IndexError):
                        status_data["status"] = "connected"

                    self.ad.options.mode = "manual"
                    self.ad.options.manual_cmd = "disable_xy"
                    self.ad.plot_run()
                    self.ad.disconnect()

            self.last_known_status = status_data.copy()
            if self.last_usb_id:
                self.device_cache[self.last_usb_id] = status_data.copy()

            return status_data
        finally:
            self.sem.release()