/*
 * RoboUAQ 2026 - ESP32 Motor Controller Firmware
 * ================================================
 * Platform: ESP32 DevKit V1 (WROOM-32), Arduino framework, ESP32 core v3.x
 * Build:    PlatformIO (board: esp32doit-devkit-v1)
 *
 * Features:
 *   - Quadrature encoder (Phase A + B) with direction detection
 *   - PID velocity control at 100 Hz on Core 1
 *   - Direct servo PWM (no PCA9685)
 *   - TB6612FNG motor driver (forward/reverse/brake)
 *   - UART2 protocol with Jetson Orin Nano
 *   - 500ms watchdog: zeros velocity + centers servo on timeout
 *   - Dual-core RTOS architecture
 *
 * UART Protocol:
 *   Jetson → ESP32:  "V<vel_m/s>,S<steer_pulse>\n"
 *   ESP32 → Jetson:  "v<vel>,s<steer>,e<enc_ticks>\n"
 *
 * GPIO Map (from KiCad schematic):
 *   D34 = Encoder Phase A    D35 = Encoder Phase B
 *   D25 = Servo PWM          D23 = PWMA (TB6612)
 *   D21 = AIN1 (TB6612)      D22 = AIN2 (TB6612)
 *   D19 = STBY (TB6612)
 *   D16 = RX2 (UART2)        D17 = TX2 (UART2)
 *
 * Power:  LiPo 3S 11.1V → Mini560 → 5V (servo, ESP32 VIN)
 *         TB6612 VM = 11.1V, VCC = 3.3V (from ESP32)
 */

#include <Arduino.h>
#include <ESP32Servo.h>

// ──────────────────────── GPIO DEFINITIONS ────────────────────────
#define ENC_A       34    // Encoder phase A (input only)
#define ENC_B       35    // Encoder phase B (input only)
#define SERVO_PIN   25    // Direct servo PWM
#define PWMA_PIN    23    // TB6612 speed (PWM)
#define AIN1_PIN    21    // TB6612 direction bit 1
#define AIN2_PIN    22    // TB6612 direction bit 2
#define STBY_PIN    19    // TB6612 standby (HIGH = active)
#define UART_RX2    16    // UART2 RX (from Jetson TX)
#define UART_TX2    17    // UART2 TX (to Jetson RX)

// ──────────────────────── ENCODER CONSTANTS ────────────────────────
#define PULSES_PER_REV  360           // Encoder resolution
#define WHEEL_DIAM_M    0.065f        // 65mm wheel diameter
#define WHEEL_CIRC_M    (PI * WHEEL_DIAM_M)  // ~0.2042 m
// With quadrature (counting both edges of A): effective 720 counts/rev
// But we count only rising edges of A + direction from B = 360 counts/rev

// ──────────────────────── PID CONSTANTS ────────────────────────
#define PID_DT          0.01f         // 100 Hz loop → 10ms
#define PID_KP          80.0f
#define PID_KI          15.0f
#define PID_KD          2.0f
#define PID_IMAX        255.0f        // Anti-windup clamp
#define PWM_MAX         255
#define ACCEL_STEP      0.02f         // Max velocity change per PID cycle (m/s)

// ──────────────────────── SERVO CONSTANTS ────────────────────────
#define SERVO_MIN_US    500           // Full left pulse width (µs)
#define SERVO_MAX_US    2500          // Full right pulse width (µs)
#define SERVO_CENTER_US 1500          // Center pulse width (µs)
#define SERVO_FREQ      50            // Standard servo frequency (Hz)

// ──────────────────────── UART CONSTANTS ────────────────────────
#define UART_BAUD       115200
#define WATCHDOG_MS     500           // Timeout: zero vel + center servo

// ──────────────────────── MOVING AVERAGE ────────────────────────
#define MA_WINDOW       5             // Sliding window size

// ──────────────────────── GLOBAL STATE ────────────────────────

// Encoder (accessed from ISR + tasks, must be volatile)
volatile int32_t encoderTicks = 0;    // Cumulative tick count (signed)
volatile int8_t  encoderDir   = 1;    // +1 forward, -1 reverse

// PID state
float targetVel   = 0.0f;            // Desired velocity (m/s) from Jetson
float currentVel  = 0.0f;            // Measured velocity (m/s)
float rampedVel   = 0.0f;            // Ramped target for smooth accel
float pidIntegral = 0.0f;
float pidPrevErr  = 0.0f;

// Moving average buffer
float maBuffer[MA_WINDOW] = {0};
int   maIndex = 0;

// Servo
Servo steeringServo;
int   targetSteerUs = SERVO_CENTER_US; // Desired servo µs from Jetson

// UART
char  rxBuf[64];
int   rxIdx = 0;
unsigned long lastCmdTime = 0;        // For watchdog

// Telemetry
unsigned long lastTelemetryTime = 0;
#define TELEMETRY_INTERVAL_MS 50      // 20 Hz telemetry

// ──────────────────────── ENCODER ISR ────────────────────────
// Rising edge on Phase A → read Phase B for direction
void IRAM_ATTR encoderISR() {
    if (digitalRead(ENC_B)) {
        encoderTicks -= 1;  // Use -= 1 instead of -- to avoid volatile warnings
        encoderDir = -1;
    } else {
        encoderTicks += 1;  // Use += 1 instead of ++ to avoid volatile warnings
        encoderDir = 1;
    }
}

// ──────────────────────── MOTOR CONTROL ────────────────────────
void motorSetPWM(int pwmVal) {
    // pwmVal: -255 to +255 (negative = reverse)
    pwmVal = constrain(pwmVal, -PWM_MAX, PWM_MAX);

    if (pwmVal > 0) {
        // Forward: AIN1=HIGH, AIN2=LOW
        digitalWrite(AIN1_PIN, HIGH);
        digitalWrite(AIN2_PIN, LOW);
        ledcWrite(0, pwmVal);
    } else if (pwmVal < 0) {
        // Reverse: AIN1=LOW, AIN2=HIGH
        digitalWrite(AIN1_PIN, LOW);
        digitalWrite(AIN2_PIN, HIGH);
        ledcWrite(0, -pwmVal);
    } else {
        // Brake: AIN1=LOW, AIN2=LOW, PWM=0
        digitalWrite(AIN1_PIN, LOW);
        digitalWrite(AIN2_PIN, LOW);
        ledcWrite(0, 0);
    }
}

void motorStop() {
    motorSetPWM(0);
}

// ──────────────────────── MOVING AVERAGE ────────────────────────
float movingAverage(float newVal) {
    maBuffer[maIndex] = newVal;
    maIndex = (maIndex + 1) % MA_WINDOW;
    float sum = 0;
    for (int i = 0; i < MA_WINDOW; i++) sum += maBuffer[i];
    return sum / MA_WINDOW;
}

// ──────────────────────── UART PARSING ────────────────────────
// Format: "V<float>,S<int>\n"  e.g. "V0.30,S1500\n"
void parseCommand(const char* cmd) {
    float vel = 0;
    int steer = SERVO_CENTER_US;

    if (cmd[0] == 'V') {
        char* comma = strchr(cmd, ',');
        if (comma) {
            vel = atof(cmd + 1);
            if (*(comma + 1) == 'S') {
                steer = atoi(comma + 2);
            }
        }
    }

    // Validate ranges
    targetVel = constrain(vel, -1.0f, 1.0f);       // ±1 m/s max
    targetSteerUs = constrain(steer, SERVO_MIN_US, SERVO_MAX_US);
    lastCmdTime = millis();
}

// ──────────────────────── RTOS TASKS ────────────────────────

// Task: Read encoder + compute velocity (Core 0, priority 1)
void tareaEncoder(void* param) {
    TickType_t xLastWake = xTaskGetTickCount();
    int32_t prevTicks = 0;

    for (;;) {
        vTaskDelayUntil(&xLastWake, pdMS_TO_TICKS(10)); // 100 Hz

        // Read ticks atomically
        noInterrupts();
        int32_t ticks = encoderTicks;
        interrupts();

        int32_t delta = ticks - prevTicks;
        prevTicks = ticks;

        // Convert to m/s: delta_ticks / PPR * circumference / dt
        float rawVel = (float)delta / PULSES_PER_REV * WHEEL_CIRC_M / PID_DT;
        currentVel = movingAverage(rawVel);
    }
}

// Task: PID control loop + servo (Core 1, priority 3)
void tareaPID(void* param) {
    TickType_t xLastWake = xTaskGetTickCount();

    for (;;) {
        vTaskDelayUntil(&xLastWake, pdMS_TO_TICKS(10)); // 100 Hz

        // Watchdog check
        if (millis() - lastCmdTime > WATCHDOG_MS) {
            targetVel = 0.0f;
            targetSteerUs = SERVO_CENTER_US;
        }

        // Acceleration ramp
        if (rampedVel < targetVel) {
            rampedVel = min(rampedVel + ACCEL_STEP, targetVel);
        } else if (rampedVel > targetVel) {
            rampedVel = max(rampedVel - ACCEL_STEP, targetVel);
        }

        // PID computation
        float error = rampedVel - currentVel;
        pidIntegral += error * PID_DT;
        pidIntegral = constrain(pidIntegral, -PID_IMAX, PID_IMAX);
        float derivative = (error - pidPrevErr) / PID_DT;
        pidPrevErr = error;

        float output = PID_KP * error + PID_KI * pidIntegral + PID_KD * derivative;

        // If target is zero, force output to zero (clean stop)
        if (fabsf(rampedVel) < 0.001f && fabsf(currentVel) < 0.01f) {
            output = 0;
            pidIntegral = 0;
        }

        motorSetPWM((int)constrain(output, -PWM_MAX, PWM_MAX));

        // Update servo
        steeringServo.writeMicroseconds(targetSteerUs);
    }
}

// Task: UART communication with Jetson (Core 0, priority 2)
void tareaROS(void* param) {
    for (;;) {
        // Read incoming commands
        while (Serial2.available()) {
            char c = Serial2.read();
            if (c == '\n' || c == '\r') {
                if (rxIdx > 0) {
                    rxBuf[rxIdx] = '\0';
                    parseCommand(rxBuf);
                    rxIdx = 0;
                }
            } else if (rxIdx < (int)sizeof(rxBuf) - 1) {
                rxBuf[rxIdx] = c;
                rxIdx += 1;  // Use += 1 instead of ++ for volatile safety pattern
            }
        }

        // Send telemetry at fixed rate
        unsigned long now = millis();
        if (now - lastTelemetryTime >= TELEMETRY_INTERVAL_MS) {
            lastTelemetryTime = now;

            noInterrupts();
            int32_t ticks = encoderTicks;
            interrupts();

            // Format: "v<vel>,s<steer>,e<ticks>\n"
            char telBuf[64];
            snprintf(telBuf, sizeof(telBuf), "v%.3f,s%d,e%ld\n",
                     currentVel, targetSteerUs, (long)ticks);
            Serial2.print(telBuf);
        }

        vTaskDelay(pdMS_TO_TICKS(5)); // ~200 Hz check rate
    }
}

// ──────────────────────── SETUP ────────────────────────
void setup() {
    // USB Serial for debug
    Serial.begin(115200);
    Serial.println("[RoboUAQ] Initializing...");

    // UART2 to Jetson
    Serial2.begin(UART_BAUD, SERIAL_8N1, UART_RX2, UART_TX2);

    // Encoder pins (input only, no pullup needed — external)
    pinMode(ENC_A, INPUT);
    pinMode(ENC_B, INPUT);
    attachInterrupt(digitalPinToInterrupt(ENC_A), encoderISR, RISING);

    // Motor driver pins
    pinMode(AIN1_PIN, OUTPUT);
    pinMode(AIN2_PIN, OUTPUT);
    pinMode(STBY_PIN, OUTPUT);
    digitalWrite(AIN1_PIN, LOW);
    digitalWrite(AIN2_PIN, LOW);
    digitalWrite(STBY_PIN, HIGH);  // Enable motor driver

    // Motor PWM — ESP32 core v2.x API
    ledcSetup(0, 20000, 8);          // channel 0, 20kHz, 8-bit resolution
    ledcAttachPin(PWMA_PIN, 0);
    ledcWrite(0, 0);

    // Servo — using ESP32Servo library
    steeringServo.setPeriodHertz(SERVO_FREQ);
    steeringServo.attach(SERVO_PIN, SERVO_MIN_US, SERVO_MAX_US);
    steeringServo.writeMicroseconds(SERVO_CENTER_US);

    // Initialize watchdog timer
    lastCmdTime = millis();

    // Create RTOS tasks
    // Core 0: Encoder reading + UART communication
    xTaskCreatePinnedToCore(tareaEncoder, "Encoder", 2048, NULL, 1, NULL, 0);
    xTaskCreatePinnedToCore(tareaROS,     "ROS",     4096, NULL, 2, NULL, 0);
    // Core 1: PID control + servo (highest priority, real-time)
    xTaskCreatePinnedToCore(tareaPID,     "PID",     4096, NULL, 3, NULL, 1);

    Serial.println("[RoboUAQ] Ready. Waiting for Jetson commands...");

    // Delete the Arduino loop task — everything runs in RTOS tasks
    vTaskDelete(NULL);
}

void loop() {
    // Never reached — deleted in setup()
}
