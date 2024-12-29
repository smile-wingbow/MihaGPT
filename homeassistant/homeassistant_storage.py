from tinydb import TinyDB, Query
import json
from requests import post, get
import os
import re
import time
import pytz
from datetime import datetime
from mihagpt.config import HARDWARE_COMMAND_DICT, HARDWARE_MODEL_RUN_PERFECTLY_DICT, HARDWARE_MODEL_RUN_AVAILABLE_DICT, HARDWARE_MODEL_RUN_UNCERTAIN_DICT

# 查询所有区域列表：
# {
# "template": "{% set area_list = areas() %}[{% for area_id in area_list %}{\"area_id\": \"{{ area_id }}\",\"area_name\": \"{{ area_name(area_id) }}\"}{% if not loop.last %},{% endif %}{% endfor %}]"
# }
#
# 查询区域下的所有设备：
# {
#   "template": "{% set area_name_or_id = \"hui_yi_shi\" %}{% set device_list = area_devices(area_name_or_id) %}[{% for device_id in device_list %}{\"device_id\": \"{{ device_id }}\",\"device_name\": \"{{ device_attr(device_id, 'name') }}\",\"manufacturer\": \"{{ device_attr(device_id, 'manufacturer') }}\",\"model\": \"{{ device_attr(device_id, 'model') }}\",\"sw_version\": \"{{ device_attr(device_id, 'sw_version') }}\"{% set attributes = device_attr(device_id, 'attributes') %}{% if attributes %}{% for attr_name, attr_value in attributes.items() %}, \"{{ attr_name }}\": \"{{ attr_value }}\"{% endfor %}{% endif %}}{% if not loop.last %},{% endif %}{% endfor %}]"
# }
#
# 查询设备下的所有实体及属性：
# {
#   "template": "{% set device_id = \"162511d0a6dd55ce1bf94f210d2e99e0\" %}{% set entity_list = device_entities(device_id) %}[{% for entity_id in entity_list %}{\"entity_id\": \"{{ entity_id }}\", \"state\": \"{{ states[entity_id].state }}\", \"last_changed\": \"{{ states[entity_id].last_changed }}\", \"last_updated\": \"{{ states[entity_id].last_updated }}\", \"context_id\": \"{{ states[entity_id].context.id }}\", \"context_parent_id\": \"{{ states[entity_id].context.parent_id }}\", \"context_user_id\": \"{{ states[entity_id].context.user_id }}\", {% set attributes = states[entity_id].attributes %}{% for attr_name, attr_value in attributes.items() %}\"{{ attr_name }}\": \"{{ attr_value }}\"{% if not loop.last %},{% endif %}{% endfor %}}{% if not loop.last %},{% endif %}{% endfor %}]"
# }

# 查询设备下的所有实体id列表：
# {
#   "template": "{% set device_id = '54eb290629a5bb1df2f6431eecf712f6' %}{% set entity_list = device_entities(device_id) %}['{% for entity_id in entity_list %}{{ entity_id }}{% if not loop.last %},{% endif %}{% endfor %}']"
# }

XIAOMI_MIOT_SPEC_ADDRESS = "https://miot-spec.org/miot-spec-v2/instance?type="

XIAOMI_MIOT_SPEC_ALL_ADDRESS = "https://miot-spec.org/miot-spec-v2/instances?status=all"

class HaStorage:
    """
    持久化存储通过home assistant接口获取的区域列表、设备列表、实体列表和服务列表
    """
    area_db: TinyDB
    device_db: TinyDB
    entity_db: TinyDB
    domain_service_db: TinyDB

    device_miot_model_db: TinyDB
    device_miot_spec_db: TinyDB

    speaker_db: TinyDB

    def __init__(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.area_db = TinyDB(os.path.join(current_dir, 'areas.json'))
        self.device_db = TinyDB(os.path.join(current_dir, 'devices.json'))
        self.entity_db = TinyDB(os.path.join(current_dir, 'entities.json'))
        self.domain_service_db = TinyDB(os.path.join(current_dir, 'domain_services.json'))

        self.device_miot_model_db = TinyDB(os.path.join(current_dir, 'device_miot_model_db.json'))
        self.device_miot_spec_db = TinyDB(os.path.join(current_dir, 'device_miot_spec_db.json'))

        self.speaker_db = TinyDB(os.path.join(current_dir, 'speaker_db.json'))


    # 把时间格式统一转为本地时区
    def convert_utc_to_local(self, json_data, local_tz='Asia/Shanghai'):
        # 正则表达式匹配 UTC 时间格式
        utc_time_pattern = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+00:00')
        local_timezone = pytz.timezone(local_tz)

        def convert_time(match):
            utc_time_str = match.group(0)
            # 将时间字符串转换为 datetime 对象
            utc_dt = datetime.fromisoformat(utc_time_str.replace('Z', '+00:00'))
            # 将 UTC 时间转换为本地时区时间
            local_dt = utc_dt.astimezone(local_timezone)
            return local_dt.isoformat()

        # 转换 JSON 数据中的所有 UTC 时间字符串
        json_str = json.dumps(json_data)
        converted_json_str = utc_time_pattern.sub(convert_time, json_str)
        return json.loads(converted_json_str)

    # 初始化ha设备本地数据库
    def init_data(self, ha_address, ha_port, token, speakers, force=False):
        if force or (not (self.entity_db.all() and self.domain_service_db.all())):
            # 初始化服务列表
            domain_service_list = self.get_domain_service_list_rest_api(ha_address, ha_port, token)
            if domain_service_list:
                self.save_domain_services(domain_service_list)

            area_list = self.get_area_list_rest_api(ha_address, ha_port, token)
            if area_list:
                self.area_db.truncate()
                self.device_db.truncate()
                self.entity_db.truncate()

                self.save_areas(area_list)

            # 智能音箱列表
            speaker_list = []

            areas = self.get_all_areas()
            if areas:
                for area in areas:
                    area_id = area["area_id"]
                    device_list = self.get_device_list_by_area_id_rest_api(area_id, ha_address, ha_port, token)
                    if device_list:
                        self.save_devices(area_id, device_list)

                    devices = self.get_devices_by_area_id(area_id)
                    if devices:
                        for device in devices:
                            device_id = device["device_id"]

                            entity_id_list = self.get_entity_id_list_rest_api(device_id, ha_address, ha_port, token)
                            if entity_id_list:
                                for entity_id in entity_id_list:
                                    entity = self.get_entity_by_id_rest_api(entity_id, ha_address, ha_port, token)

                                    attributes = entity["attributes"]
                                    supported_features = "0"
                                    if "supported_features" in attributes and attributes["supported_features"]:
                                        supported_features = attributes["supported_features"]
                                    domain = entity_id.split(".")[0]

                                    if domain == "media_player" and "xiaoai_id" in attributes:
                                        xiaoai_id = attributes["xiaoai_id"]
                                        # 判断实体是否属于智能音箱且实体类型属于media_player
                                        if speakers:
                                            for item in speakers:
                                                if xiaoai_id == item["deviceID"]:
                                                    # 初始化智能音箱列表
                                                    speaker = {}

                                                    speaker["ha_device_id"] = device_id
                                                    speaker["model"] = device["model"]
                                                    speaker["area_name"] = area["area_name"]
                                                    speaker["area_id"] = area_id
                                                    speaker["xiaoai_id"] = xiaoai_id
                                                    speaker["mi_did"] = item["miotDID"]
                                                    speaker["hardware"] = item["hardware"]

                                                    if device["model"] in HARDWARE_MODEL_RUN_PERFECTLY_DICT:
                                                        speaker["run_type"] = 1
                                                        speaker["use_command"] = False
                                                        item["run_type"] = 1
                                                        item["use_command"] = False
                                                    elif device["model"] in HARDWARE_MODEL_RUN_AVAILABLE_DICT:
                                                        speaker["run_type"] = 2
                                                        speaker["use_command"] = True
                                                        item["run_type"] = 2
                                                        item["use_command"] = True
                                                    elif device["model"] in HARDWARE_MODEL_RUN_UNCERTAIN_DICT:
                                                        speaker["run_type"] = 3
                                                        speaker["use_command"] = True
                                                        item["run_type"] = 3
                                                        item["use_command"] = True
                                                    else:
                                                        speaker["run_type"] = 3
                                                        speaker["use_command"] = True
                                                        item["run_type"] = 3
                                                        item["use_command"] = True

                                                    # 写入tts_command和wakeup_command
                                                    for hardware, commands in HARDWARE_COMMAND_DICT.items():
                                                        if item["hardware"] == hardware:
                                                            speaker["tts_command"] = commands[0]
                                                            speaker["wakeup_command"] = commands[1]
                                                            item["tts_command"] = commands[0]
                                                            item["wakeup_command"] = commands[1]
                                                            break
                                                    # 如果找不到可用的tts_command和wakeup_command，则从https://miot-spec.org/中根据urn查找Intelligent Speaker
                                                    if not speaker["tts_command"] or not speaker["wakeup_command"]:
                                                        urn_model = self.get_device_info_miot_spec_rest_api(attributes["miot_type"])
                                                        if "services" in urn_model and isinstance(urn_model["services"], list):
                                                            services = urn_model["services"]
                                                            for service in services:
                                                                if "description" in service and service["description"] == "Intelligent Speaker":
                                                                    if "actions" in service and isinstance(service["actions"], list):
                                                                        actions = service["actions"]
                                                                        for action in actions:
                                                                            if "description" in action and action["description"] == "Play Text":
                                                                                speaker["tts_command"] = str(service["iid"]) + "-" + str(action["iid"])
                                                                            if "description" in action and action["description"] == "Wake Up":
                                                                                speaker["wakeup_command"] = str(service["iid"]) + "-" + str(action["iid"])
                                                                        break

                                                    speaker["media_entity_id"] = entity_id
                                                    speaker_list.append(speaker)

                                                    item["involve"] = True
                                                    item["area_name"] = area["area_name"]

                                                    break

                                    # 根据supported_features拼装设备支持的服务，以及服务的参数类型
                                    # 匹配模式的各种可能形式
                                    def find_matching_key(mode, data):
                                        possible_keys = [
                                            f"{mode}s",
                                            f"{mode}_list",
                                            mode,
                                            f"{mode}es",  # 如果复数形式是es
                                            f"xxx{mode}"
                                        ]
                                        for key in possible_keys:
                                            if key in data and isinstance(data[key], list):
                                                return key
                                        return None

                                    supported_services = self.get_services_by_supported_features(domain,
                                                                                                 supported_features)
                                    if supported_services and isinstance(supported_services, list):
                                        for service in supported_services:
                                            for key in service:
                                                mode = key.replace("set_", "")
                                                matching_key = find_matching_key(mode, attributes)
                                                if matching_key:
                                                    service[key] = attributes[matching_key]

                                    entity["supported_services"] = supported_services

                                    # if int(supported_features) > 0:
                                    #     supported_services = self.get_services_by_supported_features(domain, supported_features)
                                    #     # 把{'turn_on': [256], 'turn_off': [128], 'toggle': [128, 256], 'set_hvac_mode': [], 'set_fan_mode': [8], 'set_swing_mode': [32]}
                                    #     # 转换为{'turn_on': {'values': [256]}, 'turn_off': {"values": [128]}, 'toggle': {'values': [128, 256]}, 'set_hvac_mode': {'values': [], 'options': ["heat","dry","fan_only","auto","off"]}, 'set_fan_mode': {'values': [8], 'options': ['auto','level1','level2','level3']}, 'set_swing_mode': {'values':[32],'options':['off','vertical']}}
                                    #     converted_services = {}
                                    #     for key, value in supported_services.items():
                                    #         converted_services[key] = {"values": value}
                                    #         options = {}
                                    #         mode = key.replace("set_", "")
                                    #         matching_key = find_matching_key(mode, attributes)
                                    #         if matching_key:
                                    #             options[mode] = attributes[matching_key]
                                    #             converted_services[key]["options"] = options[mode]
                                    #         else:
                                    #             converted_services[key]["options"] = []
                                    #
                                    #     entity["supported_services"] = converted_services
                                    # else:
                                    #     entity["supported_services"] = {}

                                    self.save_entity(device_id, entity)

            if speaker_list:
                self.save_speakers(speaker_list)

    # 初始化miot spec型号信息库
    def init_miot_model_db(self):
        if not self.device_miot_model_db.all():
            miot_spec_device_info_list = self.get_all_devices_miot_spec_rest_api()
            instances = miot_spec_device_info_list["instances"]
            if instances and isinstance(instances, list):
                self.device_miot_model_db.truncate()
                self.device_miot_model_db.insert_multiple(instances)

    # 根据miot设备型号返回miot spec型号信息列表
    def get_device_model_list_miot_spec(self, device_model):
        Model = Query()
        device_info_list = self.device_miot_model_db.search(Model.model == device_model)
        return device_info_list

    # 根据miot设备型号返回miot spec规格列表
    def get_device_info_miot_spec_by_model(self, device_model):
        device_model_list = self.get_device_model_list_miot_spec(device_model)

        if device_model_list and isinstance(device_model_list, list):
            released_models = [item for item in device_model_list if item["status"] == 'released']
            max_version_model = max(released_models, key=lambda x: x["version"])
            if max_version_model:
                Urn = Query()
                device_miot_spec = self.device_miot_spec_db.search(Urn.type == max_version_model["type"])
                if device_miot_spec:
                    return device_miot_spec[0]
                else:
                    device_miot_spec = self.get_device_info_miot_spec_rest_api(max_version_model["type"])
                    self.device_miot_spec_db.insert(device_miot_spec)
                    return device_miot_spec

    # 通过ha的rest api获取区域列表
    def get_area_list_rest_api(self, ha_address, ha_port, token):
        url = "http://" + ha_address + ":" + ha_port + "/api/template"
        headers = {
            "Authorization": "Bearer " + token
        }
        area_template = {
            "template": "{% set area_list = areas() %}[{% for area_id in area_list %}{\"area_id\": \"{{ area_id }}\",\"area_name\": \"{{ area_name(area_id) }}\"}{% if not loop.last %},{% endif %}{% endfor %}]"
        }

        response = post(url, headers=headers, json=area_template)

        if response.status_code == 200:
            # print(response.text)

            # 解析 JSON 字符串为 Python 对象
            area_list = json.loads(response.text)

            return area_list
        else:
            print(f"Error: Received response with status code {response.status_code}")

        return None

    # 通过ha的rest api获取设备列表
    def get_device_list_by_area_id_rest_api(self, area_id, ha_address, ha_port, token):
        url = "http://" + ha_address + ":" + ha_port + "/api/template"
        headers = {
            "Authorization": "Bearer " + token
        }
        # Jinja 模板字符串，使用占位符
        device_template = "{% set area_name_or_id = \"__AREA_ID__\" %}{% set device_list = area_devices(area_name_or_id) %}[{% for device_id in device_list %}{\"device_id\": \"{{ device_id }}\",\"device_name\": \"{{ device_attr(device_id, 'name') }}\",\"manufacturer\": \"{{ device_attr(device_id, 'manufacturer') }}\",\"model\": \"{{ device_attr(device_id, 'model') }}\",\"sw_version\": \"{{ device_attr(device_id, 'sw_version') }}\"{% set attributes = device_attr(device_id, 'attributes') %}{% if attributes %}{% for attr_name, attr_value in attributes.items() %}, \"{{ attr_name }}\": \"{{ attr_value }}\"{% endfor %}{% endif %}}{% if not loop.last %},{% endif %}{% endfor %}]"

        # 替换占位符
        device_template = device_template.replace("__AREA_ID__", area_id)

        # 发送 API 请求
        response = post(url, headers=headers, json={"template": device_template})

        if response.status_code == 200:
            # print(response.text)

            # 解析 JSON 字符串为 Python 对象
            device_list = json.loads(response.text)

            return device_list
        else:
            print(f"Error: Received response with status code {response.status_code}")

        return None

    # 通过ha的rest api获取实体列表
    def get_entity_id_list_rest_api(self, device_id, ha_address, ha_port, token):
        url = "http://" + ha_address + ":" + ha_port + "/api/template"
        headers = {
            "Authorization": "Bearer " + token
        }
        # Jinja 模板字符串，使用占位符
        entity_template = "{% set device_id = '__DEVICE_ID__' %}{% set entity_list = device_entities(device_id) %}['{% for entity_id in entity_list %}{{ entity_id }}{% if not loop.last %},{% endif %}{% endfor %}']"
        # 替换占位符
        entity_template = entity_template.replace("__DEVICE_ID__", device_id)
        data = {
          "template": entity_template
        }

        response = post(url, headers=headers, json=data)

        if response.status_code == 200:
            # print(response.text)

            # 去掉外部的单引号，并转换为 Python 列表
            response_list_str = response.text.strip("[]").strip("'")
            # 将逗号分隔的字符串转换为列表
            entity_id_list = response_list_str.split(",")

            return entity_id_list
        else:
            print(f"Error: Received response with status code {response.status_code}")

        return None

    # 通过ha的rest api获取实体信息
    def get_entity_by_id_rest_api(self, entity_id, ha_address, ha_port, token):
        url = "http://" + ha_address + ":" + ha_port + "/api/states/" + entity_id
        headers = {
            "Authorization": "Bearer " + token
        }
        response = get(url, headers=headers)

        if response.status_code == 200:
            # print(response.text)
            response_local = self.convert_utc_to_local(response.text)

            entity = json.loads(response_local)
            return entity
        else:
            print(f"Error: Received response with status code {response.status_code}")

        return None

    # 通过ha的rest api获取服务列表
    def get_domain_service_list_rest_api(self, ha_address, ha_port, token):
        url = "http://" + ha_address + ":" + ha_port + "/api/services"
        headers = {
            "Authorization": "Bearer " + token
        }

        response = get(url, headers=headers)

        if response.status_code == 200:
            # print(response.text)
            response_local = self.convert_utc_to_local(response.text)
            # 解析 JSON 字符串为 Python 对象
            domain_service_list = json.loads(response_local)

            return domain_service_list
        else:
            print(f"Error: Received response with status code {response.status_code}")

        return None

    # 通过小米 miot-spec获取设备miot spec规格
    def get_device_info_miot_spec_rest_api(self, urn_type):
        url = XIAOMI_MIOT_SPEC_ADDRESS + urn_type

        response = get(url)

        if response.status_code == 200:
            # print(response.text)
            response_local = self.convert_utc_to_local(response.text)
            # 解析 JSON 字符串为 Python 对象
            miot_spec_device_info = json.loads(response_local)

            return miot_spec_device_info
        else:
            print(f"Error: Received response with status code {response.status_code}")

        return None

    # 通过小米 miot-spec获取所有设备信息
    def get_all_devices_miot_spec_rest_api(self):
        url = XIAOMI_MIOT_SPEC_ALL_ADDRESS

        response = get(url)

        if response.status_code == 200:
            # print(response.text)
            response_local = self.convert_utc_to_local(response.text)
            # 解析 JSON 字符串为 Python 对象
            miot_spec_device_info_list = json.loads(response_local)

            return miot_spec_device_info_list
        else:
            print(f"Error: Received response with status code {response.status_code}")

        return None

    # 根据实体类型domain和实体支持的特性列表获取实体支持的服务列表
    def get_services_by_supported_features(self, domain, supported_features):
        supported_features_int = int(supported_features)

        # 获取domain支持的服务
        domain_services = self.get_domain_service_by_domain(domain)

        # 存储支持的服务
        supported_services = []

        if domain_services and isinstance(domain_services, list):
            services = domain_services[0]["services"]
            # 检查每个服务的特性值
            for service, content in services.items():
                supported_service = {}
                if content["target"] and content["target"]["entity"]:
                    supported = True
                    for entity in content["target"]["entity"]:
                        if entity["domain"] == domain and entity["supported_features"]:
                            if all(supported_features_int & feature for feature in entity["supported_features"]):
                                supported = True
                            else:
                                supported = False
                    if supported:
                        supported_service[service] = []
                        if content["fields"] and isinstance(content["fields"], dict):
                            for field, field_content in content["fields"].items():
                                if field != "advanced_fields":
                                    if "filter" in field_content and field_content["filter"] and "supported_features" in field_content["filter"] and field_content["filter"]["supported_features"]:
                                        if all(supported_features_int & field_feature for field_feature in field_content["filter"]["supported_features"]):
                                            supported_service[service].append(field_content["selector"])
                                else:
                                    for adv_fields, adv_field_content in field_content["fields"].items():
                                        if "filter" in adv_field_content and adv_field_content[
                                            "filter"] and "supported_features" in adv_field_content["filter"] and \
                                                adv_field_content["filter"]["supported_features"]:
                                            if all(supported_features_int & field_feature for field_feature in
                                                   adv_field_content["filter"]["supported_features"]):
                                                supported_service[service].append(adv_field_content["selector"])


                supported_services.append(supported_service)
        return supported_services

    # 在本地数据库中保存区域列表
    def save_areas(self, area_list):
        # 确保 area_list 是一个列表，并且每个元素都是字典
        if isinstance(area_list, list) and all(isinstance(item, dict) for item in area_list):
            self.area_db.insert_multiple(area_list)

    # 在本地数据库中保存设备列表
    def save_devices(self, area_id, device_list):
        # 确保 device_list 是一个列表，并且每个元素都是字典
        if isinstance(device_list, list) and all(isinstance(item, dict) for item in device_list):
            for device in device_list:
                device["area_id"] = area_id
                device_miot_spec = self.get_device_info_miot_spec_by_model(device["model"])
                if device_miot_spec:
                    device["description"] = device_miot_spec["description"]
                else:
                    device["description"] = ""
            self.device_db.insert_multiple(device_list)

    # 在本地数据库中保存实体列表
    def save_entities(self, device_id, entity_id_list):
        # 确保 entity_id_list 是一个列表，并且每个元素都是字典
        if isinstance(entity_id_list, list):
            for entity_id in entity_id_list:
                entity = self.get_entity_by_id(entity_id)
                self.save_entity(device_id, entity)

    # 在本地数据库中保存实体-服务列表
    def save_domain_services(self, domain_service_list):
        self.domain_service_db.truncate()

        # 确保 domain_service_list 是一个列表，并且每个元素都是字典
        if isinstance(domain_service_list, list) and all(isinstance(item, dict) for item in domain_service_list):
            self.domain_service_db.insert_multiple(domain_service_list)

    # 在本地数据库中保存智能音箱列表
    def save_speakers(self, speaker_list):
        # 确保 speaker_list 是一个列表，并且每个元素都是字典
        if isinstance(speaker_list, list) and all(isinstance(item, dict) for item in speaker_list):
            self.speaker_db.insert_multiple(speaker_list)

    # 在本地数据库中保存区域
    def save_area(self, area):
        self.area_db.insert(area)

    # 在本地数据库中保存设备
    def save_device(self, area_id, device):
        device["area_id"] = area_id
        self.device_db.insert(device)

    # 在本地数据库中保存实体
    def save_entity(self, device_id, entity):
        entity["device_id"] = device_id
        self.entity_db.insert(entity)

    def update_entity(self, entity_id, update):
        Entity = Query()
        self.entity_db.upsert(update, Entity.entity_id == entity_id)

    # 在本地数据库中保存实体-服务
    def save_domain_service(self, domain_service):
        self.domain_service_db.insert(domain_service)

    # 从本地数据库中查询所有区域
    def get_all_areas(self):
        return self.area_db.all()

    # 从本地数据库中根据区域id查询区域
    def get_area_by_id(self, area_id):
        Area = Query()
        return self.area_db.search(Area.area_id == area_id)

    # 从本地数据库中根据区域id列表查询区域列表
    def get_areas_by_id_list(self, area_id_list):
        if area_id_list:
            Area = Query()
            return self.area_db.search(Area.area_id.one_of(area_id_list))
        else:
            return self.get_all_areas()

    # 在本地数据库中查询区域下的所有设备
    def get_devices_by_area_id(self, area_id):
        Device = Query()
        return self.device_db.search(Device.area_id == area_id)

    # 在本地数据库中根据设备id查询设备
    def get_device_by_id(self, device_id):
        Device = Query()
        return self.device_db.search(Device.device_id == device_id)

    # 在本地数据库中查询设备属性
    def get_device_property(self, device_id, property_name):
        Device = Query()
        result = self.device_db.search(Device.device_id == device_id)
        if result:
            device = result[0]
            return device.get(property_name, None)
        return None

    # 在本地数据库中查询设备下的所有实体
    def get_entities_by_device_id(self, device_id):
        Entity = Query()
        return self.entity_db.search(Entity.device_id == device_id)

    # 在本地数据库中根据实体id查询实体
    def get_entity_by_id(self, entity_id):
        Entity = Query()
        return self.entity_db.search(Entity.entity_id == entity_id)

    # 在本地数据库中查询实体的属性
    def get_entity_property(self, entity_id, property_name):
        Entity = Query()
        result = self.entity_db.search(Entity.entity_id == entity_id)
        if result:
            entity = result[0]
            return entity.get(property_name, None)
        return None

    # 在本地数据库中查询所有的实体类型domain
    def get_all_domain_services(self):
        return self.domain_service_db.all()

    # 在本地数据库中根据实体类型domain查询支持的服务
    def get_domain_service_by_domain(self, domain):
        Domain_service = Query()
        return self.domain_service_db.search(Domain_service.domain == domain)

    # 获取在设备-实体列表，格式为：[{"设备名称":[{"设备实体名称":[实体支持的服务列表]}]}]
    def get_devices_entity_list(self):
        devices_entity_list = {
            "devices": []
        }
        areas = self.get_all_areas()
        if areas:
            for area in areas:
                area_info = {}
                area_id = area["area_id"]
                area_name = area["area_name"]
                area_info[area_name] = []
                devices = self.get_devices_by_area_id(area_id)
                if devices:
                    for device in devices:
                        device_info = {
                            "device_name": device["device_name"],
                            "device_entities": []
                        }
                        device_id = device["device_id"]
                        entities = self.get_entities_by_device_id(device_id)
                        if entities:
                            for entity in entities:
                                device_info["device_entities"].append({
                                    "entity_id": entity["entity_id"],
                                    "services": entity["supported_services"]
                                })
                        area_info[area_name].append(device_info)
                devices_entity_list["devices"].append(area_info)
        return devices_entity_list

    # 获取设备-实体列表，格式为：[{"设备名称":{"description": "description of device", "entities":[{"设备实体名称":{'service':[实体支持的服务列表],'state':'state of entity'}}]}}]
    def get_simplified_devices_entities_with_description_list(self, area_id_list, sensor_type_list):
        simplified_devices = []
        areas = []
        if area_id_list and isinstance(area_id_list, list):
            areas = self.get_areas_by_id_list(area_id_list)
        else:
            areas = self.get_all_areas()
        if areas:
            for area in areas:
                area_info = {}
                area_id = area["area_id"]
                area_name = area["area_name"]
                area_info[area_name] = []
                devices = self.get_devices_by_area_id(area_id)
                if devices:
                    for device in devices:
                        device_info = {}
                        device_name = device["device_name"]
                        device_info[device_name] = {}
                        device_info[device_name]["description"] = device["description"]

                        device_info[device_name]["entities"] = []
                        device_id = device["device_id"]
                        entities = self.get_entities_by_device_id(device_id)
                        if entities:
                            for entity in entities:
                                entity_info = {}
                                entity_id = entity["entity_id"]
                                entity_type = entity_id.split(".")[0]
                                if (sensor_type_list and entity_type in sensor_type_list) or not sensor_type_list:
                                    supported_services = entity.get("supported_services", {})
                                    entity_info[entity_id] = {}
                                    entity_info[entity_id]["services"] = []
                                    if supported_services:
                                        for service in supported_services:
                                            entity_info[entity_id]["services"].append(service)
                                    entity_info["state"] = entity.get("state", "")
                                    device_info[device_name]["entities"].append(entity_info)

                        area_info[area_name].append(device_info)
                simplified_devices.append(area_info)

        return simplified_devices

    # 获取设备-实体列表，格式为：[{"设备名称":[{"设备实体名称":{'service':[实体支持的服务列表],'state':'state of entity'}}]}]
    def get_simplified_devices_entity_list(self, area_id_list, sensor_type_list):
        simplified_devices = []
        areas = []
        if area_id_list and isinstance(area_id_list, list):
            areas = self.get_areas_by_id_list(area_id_list)
        else:
            areas = self.get_all_areas()
        if areas:
            for area in areas:
                area_info = {}
                area_id = area["area_id"]
                area_name = area["area_name"]
                area_info[area_name] = []
                devices = self.get_devices_by_area_id(area_id)
                if devices:
                    for device in devices:
                        device_info = {}
                        device_name = device["device_name"]
                        device_info[device_name] = []
                        device_id = device["device_id"]
                        entities = self.get_entities_by_device_id(device_id)
                        if entities:
                            for entity in entities:
                                entity_info = {}
                                entity_id = entity["entity_id"]
                                entity_type = entity_id.split(".")[0]
                                if (sensor_type_list and entity_type in sensor_type_list) or not sensor_type_list:
                                    supported_services = entity.get("supported_services", {})
                                    entity_info[entity_id] = {}
                                    entity_info[entity_id]["services"] = []
                                    if supported_services:
                                        for service, details in supported_services.items():
                                            option_list = details.get("options", [])
                                            entity_info[entity_id]["services"].append({service: option_list})
                                    entity_info["state"] = entity.get("state", "")
                                    device_info[device_name].append(entity_info)

                        area_info[area_name].append(device_info)
                simplified_devices.append(area_info)

        return simplified_devices

    # 根据设备id获取设备-实体列表，格式为：[{"设备名称":[{"设备实体名称":{'service':[实体支持的服务列表],'state':'state of entity'}}]}]
    def get_simplified_devices_entity_by_device_list(self, area_id_list, sensor_type_list, device_name_list):
        def in_device_list(device_name):
            if device_name_list and isinstance(device_name_list, list):
                for device in device_name_list:
                    if device_name == device:
                        return  True
            return False

        simplified_devices = []
        areas = []
        if area_id_list and isinstance(area_id_list, list):
            areas = self.get_areas_by_id_list(area_id_list)
        else:
            areas = self.get_all_areas()
        if areas:
            for area in areas:
                area_info = {}
                area_id = area["area_id"]
                area_name = area["area_name"]
                area_info[area_name] = []
                devices = self.get_devices_by_area_id(area_id)
                if devices:
                    for device in devices:
                        device_info = {}
                        device_name = device["device_name"]
                        device_info[device_name] = []
                        device_id = device["device_id"]
                        if in_device_list(device_name):
                            entities = self.get_entities_by_device_id(device_id)
                            if entities:
                                for entity in entities:
                                    entity_info = {}
                                    entity_id = entity["entity_id"]
                                    entity_type = entity_id.split(".")[0]
                                    if (sensor_type_list and entity_type in sensor_type_list) or not sensor_type_list:
                                        supported_services = entity.get("supported_services", {})
                                        entity_info[entity_id] = {}
                                        entity_info[entity_id]["services"] = []
                                        if supported_services:
                                            for service, details in supported_services.items():
                                                option_list = details.get("options", [])
                                                entity_info[entity_id]["services"].append({service: option_list})
                                        entity_info["state"] = entity.get("state", "")
                                        device_info[device_name].append(entity_info)

                            area_info[area_name].append(device_info)
                simplified_devices.append(area_info)

        return simplified_devices

    # 根据设备id获取实体id列表，格式为：["实体id"...]
    def get_simplified_devices_entities_id_list_by_device_list(self, area_id_list, sensor_type_list, device_id_list):
        simplified_entities = []
        areas = []
        if area_id_list and isinstance(area_id_list, list):
            areas = self.get_areas_by_id_list(area_id_list)
        else:
            areas = self.get_all_areas()
        if areas:
            for area in areas:
                area_id = area["area_id"]
                devices = self.get_devices_by_area_id(area_id)
                if devices:
                    for device in devices:
                        device_name = device["device_name"]
                        device_id = device["device_id"]
                        if self.in_device_list(device_id, device_id_list):
                            entities = self.get_entities_by_device_id(device_id)
                            if entities:
                                for entity in entities:
                                    entity_id = entity["entity_id"]
                                    entity_type = entity_id.split(".")[0]
                                    if (sensor_type_list and entity_type in sensor_type_list) or not sensor_type_list:
                                        simplified_entities.append(entity_id)

        return simplified_entities

    # 获取在设备列表，格式为：[{"device_name":"name", "device_id":"id", "description":"description"}...]
    def get_simplified_device_list(self, area_id_list, device_id_list):
        simplified_devices = []
        areas = []
        if area_id_list and isinstance(area_id_list, list):
            areas = self.get_areas_by_id_list(area_id_list)
        else:
            areas = self.get_all_areas()
        if areas:
            for area in areas:
                area_info = {}
                area_id = area["area_id"]
                area_name = area["area_name"]
                area_info[area_name] = []
                devices = self.get_devices_by_area_id(area_id)
                if devices:
                    for device in devices:
                        if not device_id_list:
                            device_info = {
                                "device_name": device["device_name"],
                                "device_id": device["device_id"],
                                "description": device["description"]
                            }
                            area_info[area_name].append(device_info)
                        elif isinstance(device_id_list, list) and self.in_device_list(device["device_id"], device_id_list):
                            device_info = {
                                "device_name": device["device_name"],
                                "device_id": device["device_id"],
                                "description": device["description"]
                            }
                            area_info[area_name].append(device_info)
                simplified_devices.append(area_info)

        return simplified_devices

    def in_device_list(self, device_id, device_id_list):
        if device_id_list and isinstance(device_id_list, list):
            for device in device_id_list:
                if device_id == device:
                    return  True
        return False


# ha_address = "192.168.1.21"
# ha_port = "8123"
# long_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJkZmNhOWQxMjljYjc0ZTk1YTg1OThlNmYzMjhmYjZlNSIsImlhdCI6MTcyMDc1OTQyMiwiZXhwIjoyMDM2MTE5NDIyfQ.9-ZsqI10JDxlNWmT1p7YV_5-rjNVa-W6bo7Ry-zSeVU"
# # #
# ha_storage = HaStorage()
# # ha_storage.init_data(ha_address, ha_port, long_token, force=True)
# services = ha_storage.get_simplified_devices_entities_with_description_list([], [])
# print(services)

# my_devices = {
#     "devices": []
# }
# # 遍历本地设备数据库
# start = time.time()
# areas = ha_storage.get_all_areas()
# if areas:
#     print(areas)
#     for area in areas:
#         area_id = area["area_id"]
#         devices = ha_storage.get_devices_by_area_id(area_id)
#         if devices:
#             print(devices)
#             for device in devices:
#                 device_info = {
#                     "device_name": device["device_name"],
#                     "device_entities": []
#                 }
#                 device_id = device["device_id"]
#                 entities = ha_storage.get_entities_by_device_id(device_id)
#                 if entities:
#                     for entity in entities:
#                         device_info["device_entities"].append({
#                             "entity_id": entity["entity_id"],
#                             "services": entity["supported_services"]
#                         })
#                     print(entities)
#                 my_devices["devices"].append(device_info)
#
# print(my_devices)
# print("Time elapsed:", round(time.time() - start, 3))
#
#
#
#
#
# 遍历本地设备数据库
# start = time.time()
# simplified_devices = []
# areas = ha_storage.get_all_areas()
# if areas:
#     # print(areas)
#     for area in areas:
#         area_id = area["area_id"]
#         devices = ha_storage.get_devices_by_area_id(area_id)
#         if devices:
#             # print(devices)
#             for device in devices:
#                 device_info = {}
#                 device_name = device["device_name"]
#                 device_info[device_name] = []
#                 device_id = device["device_id"]
#                 entities = ha_storage.get_entities_by_device_id(device_id)
#                 if entities:
#                     for entity in entities:
#                         entity_info = {}
#                         entity_id = entity["entity_id"]
#                         supported_services = entity.get("supported_services", {})
#                         entity_info[entity_id] = []
#                         if supported_services:
#                             for service, details in supported_services.items():
#                                 option_list = details.get("options", [])
#                                 entity_info[entity_id].append({service: option_list})
#                         device_info[device_name].append(entity_info)
#                     # print(entities)
#                 simplified_devices.append(device_info)

# print(ha_storage.get_simplified_devices_entity_list())
# print(simplified_devices)
# print("Time elapsed:", round(time.time() - start, 3))

# start = time.time()
# print(ha_storage.get_devices_entity_list())
# print("Time elapsed:", round(time.time() - start, 3))

# print(ha_storage.get_services_by_supported_features("media_player", "22077"))
# ha_storage.init_miot_model_db()
# ha_storage.init_data(ha_address, ha_port, long_token)

# model_spec = ha_storage.get_device_info_miot_spec_by_model("lumi.motion.bmgl01")
# print(model_spec)

