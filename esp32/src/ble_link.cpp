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
  auto* status = static_cast<NimBLECharacteristic*>(status_);
  status->setValue(data, length);
  status->notify();  // no-op without a subscribed client
}

void BleLink::push(const Event& ev) {
  if (queue_ != nullptr) xQueueSend(static_cast<QueueHandle_t>(queue_), &ev, 0);
}

bool BleLink::poll(Event& out) {
  if (queue_ == nullptr) return false;
  return xQueueReceive(static_cast<QueueHandle_t>(queue_), &out, 0) == pdTRUE;
}
