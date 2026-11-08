#include "ble_link.h"

#include <Arduino.h>
#include <NimBLEDevice.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

#include "config.h"

namespace {

constexpr UBaseType_t kQueueDepth = 8;
constexpr size_t kMaxBuildStr = 16;  // docs/protocol.md: build id text is at most 16 bytes

class ServerCallbacks : public NimBLEServerCallbacks {
 public:
  explicit ServerCallbacks(BleLink& link) : link_(link) {}

  void onConnect(NimBLEServer* server) override {
    BleLink::Event ev{BleLink::Event::Type::Connected};
    link_.push(ev);
  }

  void onConnect(NimBLEServer* server, ble_gap_conn_desc* desc) override {
    // 15–30 ms, no skipped events. The central chooses the final interval.
    // Keep the 250 ms command watchdog separate from BLE's 4 s link timeout.
    server->updateConnParams(desc->conn_handle, 12, 24, 0, 400);
  }

  void onDisconnect(NimBLEServer* server) override {
    BleLink::Event ev{BleLink::Event::Type::Disconnected};
    link_.push(ev);
    NimBLEDevice::startAdvertising();
  }

 private:
  BleLink& link_;
};

class CommandCallbacks : public NimBLECharacteristicCallbacks {
 public:
  explicit CommandCallbacks(BleLink& link) : link_(link) {}

  void onWrite(NimBLECharacteristic* characteristic) override {
    NimBLEAttValue value = characteristic->getValue();
    BleLink::Event ev{BleLink::Event::Type::Payload};
    if (value.length() > sizeof(ev.data)) {
      ev.length = 0;  // oversized: forwarded as empty so the decoder rejects it
    } else {
      ev.length = static_cast<uint8_t>(value.length());
      memcpy(ev.data, value.data(), value.length());
    }
    link_.push(ev);
  }

 private:
  BleLink& link_;
};

}  // namespace

bool BleLink::begin(uint8_t motorCount, const char* buildStr) {
  queue_ = xQueueCreate(kQueueDepth, sizeof(Event));
  if (queue_ == nullptr) return false;

  NimBLEDevice::init(cfg::kDeviceName);
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);

  NimBLEServer* server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks(*this));

  NimBLEService* service = server->createService(cfg::kServiceUuid);

  NimBLECharacteristic* command = service->createCharacteristic(
      cfg::kCommandUuid, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  command->setCallbacks(new CommandCallbacks(*this));

  NimBLECharacteristic* info = service->createCharacteristic(cfg::kInfoUuid, NIMBLE_PROPERTY::READ);
  uint8_t infoValue[2 + kMaxBuildStr] = {belt::kProtocolVersion, motorCount};
  size_t infoLength = 2;
  for (const char* p = buildStr; p != nullptr && *p != '\0' && infoLength < sizeof(infoValue); ++p)
    infoValue[infoLength++] = static_cast<uint8_t>(*p);
  info->setValue(infoValue, infoLength);

  // NimBLE adds the CCCD (0x2902) for NOTIFY itself.
  NimBLECharacteristic* status =
      service->createCharacteristic(cfg::kStatusUuid, NIMBLE_PROPERTY::READ | NIMBLE_PROPERTY::NOTIFY);
  status_ = status;

  service->start();

  NimBLEAdvertising* advertising = NimBLEDevice::getAdvertising();
  advertising->addServiceUUID(cfg::kServiceUuid);
  advertising->setScanResponse(true);
  return advertising->start();
}

void BleLink::publishStatus(const uint8_t* data, size_t length) {
  if (status_ == nullptr) return;
  // Log negotiated timing only when it changes, without exposing peer addresses.
  static uint16_t lastHandle = UINT16_MAX;
  static uint16_t lastInterval = 0, lastLatency = 0, lastTimeout = 0;
  auto* server = NimBLEDevice::getServer();
  if (server->getConnectedCount() > 0) {
    auto peer = server->getPeerInfo(size_t{0});
    const auto handle = peer.getConnHandle();
    const auto interval = peer.getConnInterval();
    const auto latency = peer.getConnLatency();
    const auto timeout = peer.getConnTimeout();
    if (handle != lastHandle || interval != lastInterval || latency != lastLatency || timeout != lastTimeout) {
      Serial.printf("[ble] interval=%.2f ms latency=%u supervision=%u ms\n",
                    interval * 1.25, latency, timeout * 10);
      lastHandle = handle;
      lastInterval = interval;
      lastLatency = latency;
      lastTimeout = timeout;
    }
    const uint32_t now = millis();
    if (static_cast<uint32_t>(now - lastDiagnosticAtMs_) >= 5000) {
      lastDiagnosticAtMs_ = now;
      int8_t rssi = 0;
      const int rssiResult = ble_gap_conn_rssi(handle, &rssi);
      Serial.printf("[ble-diag] rx=%lu max_rx_gap_ms=%lu max_queue_ms=%lu dropped=%lu",
                    static_cast<unsigned long>(receivedCommands_),
                    static_cast<unsigned long>(maxReceiveGapMs_),
                    static_cast<unsigned long>(maxQueueDelayMs_),
                    static_cast<unsigned long>(droppedEvents_.load(std::memory_order_relaxed)));
      if (rssiResult == 0) Serial.printf(" rssi=%d dBm\n", rssi);
      else Serial.printf(" rssi=unavailable(%d)\n", rssiResult);
    }
  } else {
    lastHandle = UINT16_MAX;
  }
  auto* status = static_cast<NimBLECharacteristic*>(status_);
  status->setValue(data, length);
  status->notify();  // no-op without a subscribed client
}

void BleLink::push(const Event& ev) {
  Event stamped = ev;
  stamped.receivedAtMs = millis();
  if (queue_ != nullptr && xQueueSend(static_cast<QueueHandle_t>(queue_), &stamped, 0) != pdTRUE)
    droppedEvents_.fetch_add(1, std::memory_order_relaxed);
}

bool BleLink::poll(Event& out) {
  if (queue_ == nullptr) return false;
  if (xQueueReceive(static_cast<QueueHandle_t>(queue_), &out, 0) != pdTRUE) return false;
  if (out.type == Event::Type::Connected) {
    receivedCommands_ = 0;
    maxReceiveGapMs_ = 0;
    maxQueueDelayMs_ = 0;
    droppedEvents_.store(0, std::memory_order_relaxed);
    lastDiagnosticAtMs_ = millis();
  } else if (out.type == Event::Type::Payload) {
    if (receivedCommands_ > 0) {
      const uint32_t gap = out.receivedAtMs - lastReceivedAtMs_;
      if (gap > maxReceiveGapMs_) maxReceiveGapMs_ = gap;
    }
    const uint32_t delay = millis() - out.receivedAtMs;
    if (delay > maxQueueDelayMs_) maxQueueDelayMs_ = delay;
    lastReceivedAtMs_ = out.receivedAtMs;
    ++receivedCommands_;
  }
  return true;
}
