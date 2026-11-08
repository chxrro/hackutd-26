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

  // Starts advertising. `motorCount` is exposed through the info characteristic.
  bool begin(uint8_t motorCount);

  // Returns true and fills `out` while events are pending. Call from loop().
  bool poll(Event& out);

  // Called from BLE callbacks. Drops the event if the queue is full.
  void push(const Event& ev);

 private:
  void* queue_ = nullptr;  // FreeRTOS QueueHandle_t, kept opaque to keep headers light
};
