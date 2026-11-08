import SwiftUI

struct BeltBluetoothSection: View {
  @Bindable var model: AppModel
  private let names = ["0 · Far left", "1 · Left", "2 · Center", "3 · Right", "4 · Far right"]

  var body: some View {
    Section {
      Text(model.belt.message)
      if model.belt.ready || model.belt.busy {
        LabeledContent("Selected belt", value: model.belt.selectedName)
        Button("Disconnect belt", role: .destructive) { model.disconnectBelt() }
      } else {
        Button(model.belt.state == .scanning ? "Stop searching" : "Find ESP32 belt") {
          if model.belt.state == .scanning { model.belt.stopScan() }
          else { model.scanForBelt() }
        }
        ForEach(model.belt.devices) { device in
          Button {
            model.belt.connect(device.id)
          } label: {
            VStack(alignment: .leading) {
              Text(device.name)
              Text("\(device.id.uuidString.suffix(8)) · signal \(device.rssi) dBm")
                .font(.caption).foregroundStyle(.secondary)
            }
          }
        }
      }
      Toggle("Send alerts to belt", isOn: Binding(
        get: { model.beltAlertsEnabled }, set: { model.setBeltAlertsEnabled($0) }))
        .disabled(!model.belt.canRelay)
      Text(model.beltRelayStatus).font(.footnote)
      if model.belt.ready {
        Text(model.belt.confirmation).font(.footnote)
        if let info = model.belt.info { LabeledContent("Firmware build", value: info.build) }
        LabeledContent("Acknowledged writes", value: "\(model.belt.acknowledgedWrites)")
        if let ms = model.belt.lastAckMs {
          LabeledContent("Last acknowledgement", value: String(format: "%.0f ms", ms))
        }
        if let ms = model.belt.maxCommandGapMs {
          LabeledContent("Longest command gap", value: String(format: "%.0f ms", ms))
        }
        if let status = model.belt.firmware {
          LabeledContent("Firmware mode", value: status.modeName)
          LabeledContent("Firmware levels", value: status.levels.map(String.init).joined(separator: ", "))
          LabeledContent("Firmware PWM cap", value: "\(status.maxDuty)")
        }
        Picker("Test motor", selection: $model.beltTestMotor) {
          ForEach(0..<5) { index in Text(names[index]).tag(index) }
        }
        Button("Briefly test selected motor") { model.testBeltMotor() }
          .disabled(!model.belt.canRelay)
        Text("Test requests level 120 for 200 ms and turns belt alerts off. Confirm the correct motor on a bench.")
          .font(.footnote).foregroundStyle(.secondary)
      }
    } header: {
      Text("ESP32 belt · phone Bluetooth")
    } footer: {
      Text("Run the hub with --belt sim and leave its laptop BLE connection off. Keep this app open. Vibration means an obstacle in that direction, not where to walk. Disconnecting or locking may trigger the firmware's link-loss cue before it goes quiet. Acknowledgements do not prove motors moved.")
    }
  }
}
