/*
 * ESP32 超声波测距 + I2C 液晶屏显示
 * 
 * 传感器: HC-SR04
 * 显示屏: I2C LCD1602 (HW-61 / PCF8574)
 */

#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// 引脚定义
const int TRIG_PIN = 5;
const int ECHO_PIN = 18;

// LCD 设置 (地址 0x27, 16列, 2行)
LiquidCrystal_I2C lcd(0x27, 16, 2);

void setup() {
  Serial.begin(115200);

  // 初始化超声波引脚
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);

  // 初始化 LCD
  lcd.init();
  lcd.backlight();
  
  // 显示欢迎信息
  lcd.setCursor(0, 0);
  lcd.print("Distance Meter");
  lcd.setCursor(0, 1);
  lcd.print("Initializing...");
  delay(2000);
  lcd.clear();
}

void loop() {
  // 1. 触发超声波测距
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // 2. 读取回声时间 (微秒)
  long duration = pulseIn(ECHO_PIN, HIGH);

  // 3. 计算距离 (声速约 340m/s = 0.034 cm/us)
  // 距离 = (时间 * 声速) / 2 (来回)
  float distanceCm = duration * 0.034 / 2;

  // 4. 在串口打印 (用于调试)
  Serial.print("Distance: ");
  Serial.print(distanceCm);
  Serial.println(" cm");

  // 5. 在 LCD 上显示
  lcd.setCursor(0, 0);
  lcd.print("Range Finder");
  
  lcd.setCursor(0, 1);
  if (duration == 0 || distanceCm > 400) {
    // 如果超出范围或未检测到回声
    lcd.print("Out of Range    "); 
  } else {
    lcd.print("Dist: ");
    lcd.print(distanceCm, 1); // 保留一位小数
    lcd.print(" cm     ");    // 后面加空格清除旧数据的残影
  }

  // 刷新间隔
  delay(500);
}
