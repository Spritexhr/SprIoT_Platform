/*
 * ESP32 MQTT 超声波传感器上报
 *
 * 功能特性：
 * - HC-SR04 超声波测距
 * - I2C LCD 显示当前距离和传感器状态
 * - 符合 iot/sensors/{device_id}/control 控制主题格式
 * - 符合 iot/sensors/{device_id}/status 状态上报格式
 * - 支持 current_status 查询当前状态
 * - 支持 set_status_interval 设置状态上报间隔（心跳）
 * - 支持 set_data_interval 设置超声波数据采集和上报间隔
 * - 支持 enable, disable 开启/关闭测距及上报
 * - 定期上报超声波距离数据
 * - NTP 时间同步，提供准确的时间戳
 * - 自动重连机制
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <NTPClient.h>
#include <WiFiUdp.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// ============ 引脚定义 ============
#define TRIG_PIN 5           // 超声波 TRIG
#define ECHO_PIN 18          // 超声波 ECHO

// ============ LCD 设置 ============
// LCD 地址通常为 0x27, 16列, 2行
LiquidCrystal_I2C lcd(0x27, 16, 2);

// ============ 设备标识 ============
const char* DEVICE_ID = "esp32_hcsr04_001";

// ============ WiFi配置 ============
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// ============ MQTT配置 ============
const char* MQTT_SERVER = "YOUR_MQTT_BROKER_IP";
const int MQTT_PORT = 1883;
const char* MQTT_USERNAME = "";
const char* MQTT_PASSWORD = "";

String MQTT_TOPIC_CONTROL;
String MQTT_TOPIC_STATUS;
String MQTT_TOPIC_DATA;

// ============ NTP配置 ============
const char* NTP_SERVER = "ntp.aliyun.com";
const long UTC_OFFSET = 0; // 若需要北京时间可设为 8 * 3600
WiFiUDP ntpUDP;
NTPClient timeClient(ntpUDP, NTP_SERVER, UTC_OFFSET, 60000);

// ============ 全局对象 ============
WiFiClient espClient;
PubSubClient mqttClient(espClient);

// ============ 运行参数 ============
int statusReportInterval = 10;      // 状态上报间隔（秒），默认设为10秒以更快看到变化
int samplingInterval = 5000;        // 超声波数据采集间隔（毫秒），默认5000ms(5秒)
bool isEnabled = true;              // 是否启用传感器测距及上报
unsigned long lastStatusReportTime = 0;
float currentDistanceCm = 0.0;

// ============ 辅助函数 ============

void updateLCD() {
  lcd.setCursor(0, 0);
  lcd.print("Dist: ");
  if (!isEnabled) {
    lcd.print("Disabled   ");
  } else if (currentDistanceCm > 400 || currentDistanceCm < 0) {
    lcd.print("Out Range  ");
  } else {
    lcd.print(currentDistanceCm, 1);
    lcd.print(" cm       ");
  }
  
  lcd.setCursor(0, 1);
  lcd.print("Status: ");
  lcd.print(isEnabled ? "ENABLED " : "DISABLED");
}

float measureDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // 增加合理的超时时间（30000微秒约为5米），防止 pulseIn 在未检测到回波时阻塞主循环长达 1 秒
  long duration = pulseIn(ECHO_PIN, HIGH, 30000); 
  
  if (duration == 0) return -1.0; // 超时或超出范围返回 -1.0

  // 距离 = (时间 * 声速) / 2 (来回)
  return duration * 0.034 / 2;
}

void sendSensorData(float distance) {
  timeClient.update();

  StaticJsonDocument<256> doc;
  doc["sensor_id"] = DEVICE_ID;
  
  JsonObject data = doc.createNestedObject("data");
  data["distance_cm"] = distance;
  
  doc["timestamp"] = timeClient.getEpochTime();

  String jsonString;
  serializeJson(doc, jsonString);

  if (mqttClient.publish(MQTT_TOPIC_DATA.c_str(), jsonString.c_str())) {
    Serial.print(F("✓ 数据上报: dist="));
    Serial.print(distance);
    Serial.println(F("cm"));
  } else {
    Serial.println(F("✗ 数据上报失败"));
  }
}

// ============ 状态上报 ============

void sendStatusUpdate(const char* event, const char* checkCode = nullptr) {
  timeClient.update();

  StaticJsonDocument<384> doc;

  doc["sensor_id"] = DEVICE_ID;
  doc["event"] = event;
  doc["timestamp"] = timeClient.getEpochTime();

  JsonObject status = doc.createNestedObject("status");
  status["is_enabled"] = isEnabled;
  status["samplingInterval"] = samplingInterval;
  status["statusReportInterval"] = statusReportInterval;

  if (checkCode != nullptr && checkCode[0] != '\0') {
    doc["check_code"] = checkCode;
  }

  String jsonString;
  serializeJson(doc, jsonString);

  if (mqttClient.publish(MQTT_TOPIC_STATUS.c_str(), jsonString.c_str())) {
    Serial.print(F("✓ 状态上报: event="));
    Serial.print(event);
    Serial.print(F(" enabled="));
    Serial.print(isEnabled ? "true" : "false");
    Serial.print(F(" dist="));
    Serial.print(currentDistanceCm);
    Serial.println(F("cm"));
  } else {
    Serial.println(F("✗ 状态上报失败"));
  }
}

// ============ MQTT 控制命令处理 ============

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<256> doc;
  DeserializationError error = deserializeJson(doc, payload, length);

  if (error) {
    Serial.println(F("✗ JSON解析失败"));
    return;
  }

  const char* command = doc["command"];
  if (!command) return;

  const char* checkCode = doc["check_code"].as<const char*>();
  if (checkCode == nullptr) checkCode = "";

  if (strcmp(command, "current_status") == 0) {
    sendStatusUpdate("check_current_status", checkCode);
  }
  else if (strcmp(command, "set_status_interval") == 0) {
    int newInterval = doc["interval"].as<int>();
    if (newInterval >= 1 && newInterval <= 3600) {
      statusReportInterval = newInterval;
      lastStatusReportTime = millis();
      sendStatusUpdate("status_interval_updated", checkCode);
    }
  }
  else if (strcmp(command, "set_data_interval") == 0) {
    int newInterval = doc["interval"].as<int>();
    if (newInterval >= 100 && newInterval <= 60000) { // 限制在 100ms 到 60000ms (60秒)
      samplingInterval = newInterval;
      sendStatusUpdate("data_interval_updated", checkCode);
    }
  }
  else if (strcmp(command, "enable") == 0) {
    isEnabled = true;
    Serial.println(F("✓ 传感器已启用"));
    sendStatusUpdate("sensor_enabled", checkCode);
  }
  else if (strcmp(command, "disable") == 0) {
    isEnabled = false;
    Serial.println(F("✓ 传感器已禁用"));
    sendStatusUpdate("sensor_disabled", checkCode);
  }
  updateLCD();
}

void mqttReconnect() {
  while (!mqttClient.connected() && WiFi.status() == WL_CONNECTED) {
    String clientId = "ESP32-" + String(DEVICE_ID);
    bool connected = (strlen(MQTT_USERNAME) > 0) ? 
      mqttClient.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD) :
      mqttClient.connect(clientId.c_str());

    if (connected) {
      mqttClient.subscribe(MQTT_TOPIC_CONTROL.c_str());
      sendStatusUpdate("online");
      lastStatusReportTime = millis();
    } else {
      delay(5000);
    }
  }
}

// ============ 初始化函数 ============

void setupSerial() {
  Serial.begin(115200);
  delay(1000);
  Serial.println(F("\n========================================"));
  Serial.println(F("  ESP32 MQTT HC-SR04 超声波传感器"));
  Serial.println(F("========================================"));
  Serial.print(F("设备ID: "));
  Serial.println(DEVICE_ID);
}

void setupPins() {
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  Serial.println(F("✓ 引脚初始化完成"));
}

void setupLCD() {
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("System Booting..");
  Serial.println(F("✓ LCD 初始化完成"));
}

void setupWiFi() {
  Serial.print(F("连接WiFi: "));
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(F("✓ WiFi连接成功"));
    Serial.print(F("IP地址: "));
    Serial.println(WiFi.localIP());
    
    lcd.clear();
    lcd.print("WiFi Connected!");
    delay(1000);
    lcd.clear();
  } else {
    Serial.println(F("✗ WiFi连接失败"));
    lcd.clear();
    lcd.print("WiFi Failed!");
  }
}

void setupMQTT() {
  MQTT_TOPIC_CONTROL = "iot/sensors/" + String(DEVICE_ID) + "/control";
  MQTT_TOPIC_STATUS = "iot/sensors/" + String(DEVICE_ID) + "/status";
  MQTT_TOPIC_DATA = "iot/sensors/" + String(DEVICE_ID) + "/data";
  
  mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
  mqttClient.setCallback(mqttCallback);
  mqttClient.setBufferSize(512);

  Serial.println(F("✓ MQTT配置完成"));
  Serial.print(F("控制主题: "));
  Serial.println(MQTT_TOPIC_CONTROL);
  Serial.print(F("状态主题: "));
  Serial.println(MQTT_TOPIC_STATUS);
  Serial.print(F("数据主题: "));
  Serial.println(MQTT_TOPIC_DATA);
}

void setupNTP() {
  timeClient.begin();
  timeClient.update();
  Serial.println(F("✓ NTP时间同步完成"));
}

void setup() {
  setupSerial();
  setupPins();
  setupLCD();
  setupWiFi();
  setupMQTT();
  setupNTP();

  Serial.println(F("========================================"));
  Serial.println(F("系统启动完成，等待控制命令..."));
  Serial.println(F("========================================\n"));
}

// ============ 主循环 ============

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    delay(1000);
    return;
  }

  if (!mqttClient.connected()) {
    mqttReconnect();
  }
  mqttClient.loop();

  // 定期测量距离并更新 LCD
  static unsigned long lastMeasureTime = 0;
  if (millis() - lastMeasureTime >= samplingInterval) {
    if (isEnabled) {
      currentDistanceCm = measureDistance();
      sendSensorData(currentDistanceCm);
    }
    updateLCD();
    lastMeasureTime = millis();
  }

  // 定期上报状态 (心跳)
  if (millis() - lastStatusReportTime >= statusReportInterval * 1000UL) {
    sendStatusUpdate("heartbeat");
    lastStatusReportTime = millis();
  }
}
