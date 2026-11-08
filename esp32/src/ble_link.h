// BLE GATT server for hub commands. NimBLE callbacks run on the BLE host task, so
// they only enqueue events; the main loop drains them with poll().
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <belt_protocol.h>

class BleLink {
 public:
  struct Event {
    enum class Type : uint8_t { Connected, Disconnected, Payload } type;
    uint8_t length = 0;
    uint8_t data[belt::kCommandHeaderSize + belt::kMaxMotors] = {};
  };

  // Starts advertising. `motorCount` and `buildStr` are exposed through the info
  // characteristic: [protocol_version, motor_count, build id ASCII...].
  bool begin(uint8_t motorCount, const char* buildStr);

  // Sets the status characteristic's value and notifies a subscribed hub
  // (docs/protocol.md, "Status"). Call from loop().
  void publishStatus(const uint8_t* data, size_t length);

  // Returns true and fills `out` while events are pending. Call from loop().
  bool poll(Event& out);

  // Called from BLE callbacks. Drops the event if the queue is full.
  void push(const Event& ev);

 private:
  void* queue_ = nullptr;   // FreeRTOS QueueHandle_t, kept opaque to keep headers light
  void* status_ = nullptr;  // NimBLECharacteristic*
};
