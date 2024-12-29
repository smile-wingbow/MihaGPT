# 把小爱音箱接入大模型，并用来控制HA中的智能电器

# 👉 先看接入后的效果：【[把小爱音箱接入大模型，并用来控制HA中的智能电器～](https://www.bilibili.com/video/BV1NG6hYqEzP/)】
## ✨ 软件

- Homeassistan，需要安装Xiaomi Miot Auto插件。如果要使用本项目生成HA的自动化，则需要把HA安装到本项目相同的主机上，并使用以下参数启动HA：docker run -d  --name ha  -p 8123:8123  --privileged   --restart=unless-stopped   -e TZ=Asia/Shanghai   -v /data/homeassistant:/config   ghcr.io/home-assistant/home-assistant:stable
- [xiaogpt](https://github.com/yihong0618/xiaogpt)，增加了同时唤醒多个音箱的支持，并支持更多的音箱型号。
- [MetaGPT](https://github.com/geekan/MetaGPT)，主要是智能体流程。
## 获取小米音响DID

| 系统和Shell   | Linux *sh                                      | Windows CMD用户                          | Windows PowerShell用户                           |
| ---------- | ---------------------------------------------- | -------------------------------------- | ---------------------------------------------- |
| 1、安装包      | `pip install miservice_fork`                   | `pip install miservice_fork`           | `pip install miservice_fork`                   |
| 2、设置变量     | `export MI_USER=xxx` <br> `export MI_PASS=xxx` | `set MI_USER=xxx`<br>`set MI_PASS=xxx` | `$env:MI_USER="xxx"` <br> `$env:MI_PASS="xxx"` |
| 3、取得MI_DID | `micli list`                                   | `micli list`                           | `micli list`                                   |
- 注意不同shell 对环境变量的处理是不同的，尤其是powershell赋值时，可能需要双引号来包括值。
- 如果获取did报错时，请更换一下无线网络，有很大概率解决问题。

## 获取homeassistant的token
- 登录homeassistant网页版
- 进入admin-安全菜单（不同版本的ha具体路径有所区别）
- 长期访问令牌页面，创建令牌并记录

## ⚡️ 快速开始
#### Python 3.9

先git clone https://github.com/smile-wingbow/MihaGPT
以下命令都在MihaGPT路径下执行
#### 一.创建虚拟环境并激活：
```shell
python3.9 -m venv mihagpt-venv  
source mihagpt-venv/bin/activate
```
#### 二.pip安装python相关库：
```shell
pip install -r requirements.txt
```
#### 三.安装浏览器（以armbian为例）
sudo apt-get update  
sudo apt-get install firefox-esr  
wget https://github.com/mozilla/geckodriver/releases/download/v0.35.0/geckodriver-v0.35.0-linux-aarch64.tar.gz  
tar -xvzf geckodriver-v0.35.0-linux-aarch64.tar.gz  
sudo mv geckodriver /usr/local/bin/
#### 四.配置参数：
##### 1.修改metaGPT的LLM配置，配置config目录的config2.yaml、gpt4o.yaml、gpt4omini.yaml配置，代码中主要用到了gpt4o和gpt4omini两种模型，分别用于不同的智能体。
##### 2.修改miha_config.yaml，说明如下：
##### 配置项说明
| 参数                  | 说明                                                                                                       | 默认值                                                                                                    | 可选值                                                           |
| --------------------- | ---------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| hardware              | 设备型号                                                                                                   |                                                                                                           |                                                                  |
| account               | 小爱账户                                                                                                   |                                                                                                           |                                                                  |
| password              | 小爱账户密码                                                                                               |                                                                                                           |         
| cookie              | 小爱账户cookie（如果用上面密码登录可以不填）                                                                                               |                                                                                                           |        
| mi_did              | 音箱设备id，参见上述获得DID的方法                                                                                               |                                                                                                           |          
| use_command              | 使用 MI command 与小爱交互                                                                                               |                                                                                                           |           
| mute_xiaoai              | 快速停掉小爱自己的回答                                                                                               |                                                                                                           |                                            |
| openai_key            | openai的apikey                                                                                             |                                                                                                           |                                                                  |
| moonshot_api_key      | moonshot kimi 的 [apikey](https://platform.moonshot.cn/docs/api/chat#%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B) |                                                                                                           |                                                                  |
| yi_api_key            | 01 wanwu 的 [apikey](https://platform.lingyiwanwu.com/apikeys)                                             |                                                                                                           |                                                                  |
| llama_api_key         | groq 的 llama3 [apikey](https://console.groq.com/docs/quickstart)                                          |                                                                                                           |                                                                  |
| serpapi_api_key       | serpapi的key 参考 [SerpAPI](https://serpapi.com/)                                                          |                                                                                                           |                                                                  |
| glm_key               | chatglm 的 apikey                                                                                          |                                                                                                           |                                                                  |
| gemini_key            | gemini 的 apikey [参考](https://makersuite.google.com/app/apikey)                                          |                                                                                                           |                                                                  |
| gemini_api_domain     | gemini 的自定义域名 [参考](https://github.com/antergone/palm-netlify-proxy)                                |                                                                                                           |
| qwen_key              | qwen 的 apikey [参考](https://help.aliyun.com/zh/dashscope/developer-reference/api-details)                |                                                                                                           |                                                                  |
| cookie                | 小爱账户cookie （如果用上面密码登录可以不填）                                                              |                                                                                                           |                                                                  |
| mi_did                | 设备did                                                                                                    |                                                                                                           |                                                                  |
| use_command           | 使用 MI command 与小爱交互                                                                                 | `false`                                                                                                   |                                                                  |
| mute_xiaoai           | 快速停掉小爱自己的回答                                                                                     | `true`                                                                                                    |                                                                  |
| verbose               | 是否打印详细日志                                                                                           | `false`                                                                                                   |                                                                  |
| bot                   | 使用的 bot 类型，目前支持 chatgptapi,newbing, qwen, gemini                                                 | `chatgptapi`                                                                                              |                                                                  |
| tts                   | 使用的 TTS 类型                                                                                            | `mi`                                                                                                      | `edge`、 `openai`、`azure`、`volc`、`baidu`、`google`、`minimax` |
| tts_options           | TTS 参数字典，参考 [tetos](https://github.com/frostming/tetos) 获取可用参数                                |                                                                                                           |                                                                  |
| prompt                | 自定义prompt                                                                                               | `请用100字以内回答`                                                                                       |                                                                  |
| keyword               | 自定义请求词列表                                                                                           | `["请"]`                                                                                                  |                                                                  |
| change_prompt_keyword | 更改提示词触发列表                                                                                         | `["更改提示词"]`                                                                                          |                                                                  |
| start_conversation    | 开始持续对话关键词                                                                                         | `开始持续对话`                                                                                            |                                                                  |
| end_conversation      | 结束持续对话关键词                                                                                         | `结束持续对话`                                                                                            |                                                                  |
| stream                | 使用流式响应，获得更快的响应                                                                               | `true`                                                                                                    |                                                                  |
| proxy                 | 支持 HTTP 代理，传入 http proxy URL                                                                        | ""                                                                                                        |                                                                  |
| gpt_options           | OpenAI API 的参数字典                                                                                      | `{}`                                                                                                      |                                                                  |
| deployment_id         | Azure OpenAI 服务的 deployment ID                                                                          | 参考这个[如何找到deployment_id](https://github.com/yihong0618/xiaogpt/issues/347#issuecomment-1784410784) |                                                                  |
| api_base              | 如果需要替换默认的api,或者使用Azure OpenAI 服务                                                            | 例如：`https://abc-def.openai.azure.com/`                                                                 |
| volc_access_key       | 火山引擎的 access key 请在[这里](https://console.volcengine.com/iam/keymanage/)获取                        |                                                                                                           |                                                                  |
| volc_secret_key       | 火山引擎的 secret key 请在[这里](https://console.volcengine.com/iam/keymanage/)获取                        |                                                                                                           |
| debug_mode       | 在本机上调试模式                        |                                                                                                           |
|
| ha_address       | homeassistant地址                        |                                                                                                           |
|
| ha_token       | homeassistant api的token                        |                                                                                                           |
|
#### 五.启动服务：
使用以下命令启动
```shell
python3.9 mihagpt.py --config miha_config.yaml
```
## 联系
加群一起讨论

![](https://github.com/smile-wingbow/MihaGPT/blob/main/assets/wechat.jpg?raw=true)

## ❤️ 鸣谢

感谢以下项目提供的贡献：

- https://github.com/yihong0618/xiaogpt
- https://github.com/geekan/MetaGPT
- https://github.com/Yonsm/MiService

## 免责声明

本项目仅供学习和研究目的，不得用于任何商业活动。用户在使用本项目时应遵守所在地区的法律法规，对于违法使用所导致的后果，本项目及作者不承担任何责任。 本项目可能存在未知的缺陷和风险（包括但不限于设备损坏和账号封禁等），使用者应自行承担使用本项目所产生的所有风险及责任。 作者不保证本项目的准确性、完整性、及时性、可靠性，也不承担任何因使用本项目而产生的任何损失或损害责任。 使用本项目即表示您已阅读并同意本免责声明的全部内容

## License

[MIT](https://github.com/idootop/mi-gpt/blob/main/LICENSE) License © 2024-PRESENT smilewingbow

