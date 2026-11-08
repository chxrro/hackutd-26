//
// ContentView.swift
//
// One screen, three sections that mirror the system: glasses, hub, belt.
//

import SwiftUI
import AVKit

struct ContentView: View {
  @Bindable var model: AppModel
  @Environment(\.scenePhase) private var scenePhase

  var body: some View {
    NavigationStack {
      Form {
        SpeechSection(model: model)
        PhoneVibrationSection(model: model)
        PatternPracticeSection(model: model)
        BeltBluetoothSection(model: model)
        GlassesSection(glasses: model.glasses, preview: model.preview, stopCamera: model.stopCamera)
        HubSection(model: model)
        BeltSection(model: model)
        Section {
          Text("Experimental prototype. Silence does not mean the path is clear.")
            .font(.footnote)
            .foregroundStyle(.secondary)
        }
      }
      .navigationTitle("Haptic Belt")
    }
    .onChange(of: scenePhase, initial: true) { _, phase in model.setAppActive(phase == .active) }
    .alert(
      "Glasses",
      isPresented: Binding(
        get: { model.glasses.errorMessage != nil },
        set: { if !$0 { model.glasses.errorMessage = nil } }
      )
    ) {
      Button("OK") { model.glasses.errorMessage = nil }
    } message: {
      Text(model.glasses.errorMessage ?? "")
    }
    .alert(
      "Hub",
      isPresented: Binding(get: { model.hubError != nil }, set: { if !$0 { model.hubError = nil } })
    ) {
      Button("OK") { model.hubError = nil }
    } message: {
      Text(model.hubError ?? "")
    }
  }
}

private struct AudioOutputPicker: UIViewRepresentable {
  let output: SpeechOutput

  func makeCoordinator() -> Coordinator { Coordinator(output: output) }
  func makeUIView(context: Context) -> AVRoutePickerView {
    let view = AVRoutePickerView()
    view.prioritizesVideoDevices = false
    view.delegate = context.coordinator
    view.accessibilityLabel = "Choose audio output"
    return view
  }
  func updateUIView(_ view: AVRoutePickerView, context: Context) {}

  final class Coordinator: NSObject, AVRoutePickerViewDelegate {
    let output: SpeechOutput
    init(output: SpeechOutput) { self.output = output }
    func routePickerViewDidEndPresentingRoutes(_ routePickerView: AVRoutePickerView) {
      let output = output
      Task { @MainActor in output.refreshRoute() }
    }
  }
}

private struct SpeechSection: View {
  @Bindable var model: AppModel

  var body: some View {
    Section {
      Button("Test glasses speech") { model.testGlassesSpeech() }
      HStack {
        Text("Choose audio output")
        Spacer()
        AudioOutputPicker(output: model.speechOutput)
          .frame(width: 44, height: 44)
          .task { _ = model.speechOutput.prepare(requestRoute: false) }
      }
      LabeledContent("Audio output", value: model.speechOutput.routeName)
      LabeledContent("Audio volume", value: "\(Int(model.speechOutput.volume * 100))%")
      Toggle("Speak obstacle alerts", isOn: Binding(
        get: { model.speechEnabled }, set: { model.setSpeechEnabled($0) }))
      Toggle("Include object and motion", isOn: $model.detailedSpeech)
      Toggle("Repeat ongoing alerts", isOn: $model.speechReminders)
      Button("Repeat current alert") { model.repeatCurrentAlert() }
        .disabled(!model.speechEnabled || !model.speechOutput.bluetoothReady)
      Text(model.speechOutput.error ?? model.speechStatus)
        .foregroundStyle(model.speechOutput.error == nil ? Color.primary : Color.red)
      Text(model.speechOutput.playbackStatus)
        .font(.footnote)
      Text("Last requested: \(model.speechOutput.lastPhrase)")
        .font(.footnote)
    } header: {
      Text("Glasses speech")
    } footer: {
      Text("Choose your glasses as the audio output using the picker above. Bluetooth pairing alone does not select audio. Test while the camera is running. Directions follow the camera, not your torso. Speech describes obstacles, not where to walk. Automatic speech pauses for VoiceOver. Keep the app open.")
    }
  }
}

private struct PhoneVibrationSection: View {
  @Bindable var model: AppModel

  var body: some View {
    Section {
      Toggle("Vibrate phone for alerts", isOn: Binding(
        get: { model.phoneVibrationEnabled }, set: { model.setPhoneVibrationEnabled($0) }))
        .disabled(!model.phoneHaptics.supported)
      Text(model.phoneHaptics.error ?? model.phoneVibrationStatus)
        .foregroundStyle(model.phoneHaptics.error == nil ? Color.primary : Color.red)
      Button("Test phone vibration") { model.testPhoneVibration() }
        .disabled(!model.phoneHaptics.supported)
      VStack(alignment: .leading) {
        Text("Phone strength: \(Int(model.phoneStrength * 100))%")
        Slider(value: $model.phoneStrength, in: 0.2...1, step: 0.1)
          .accessibilityLabel("Phone vibration strength")
          .accessibilityValue("\(Int(model.phoneStrength * 100)) percent")
      }
    } header: {
      Text("Phone vibration")
    } footer: {
      Text("Single buzz = obstacle. Faster double buzz = higher urgency. Three taps = alerts unavailable. Voice gives obstacle direction; the phone cannot reproduce the belt's left/right cues. Strength changes the phone only. Keep the app open.")
    }
  }
}

private struct PatternPracticeSection: View {
  @Bindable var model: AppModel

  var body: some View {
    Section("Learn the alert patterns") {
      Picker("Pattern", selection: $model.patternSample) {
        ForEach(AlertPatternSample.allCases) { sample in
          Text(sample.title).tag(sample)
        }
      }
      Button("Play selected test") { model.testAlertPattern() }
      Text(model.patternTestStatus).font(.footnote)
      Text("Hold the phone while stationary. Tests are labeled aloud through your selected Bluetooth output. Re-enable automatic alerts when finished.")
        .font(.footnote).foregroundStyle(.secondary)
    }
  }
}

// MARK: - Glasses

private struct GlassesSection: View {
  let glasses: GlassesController
  let preview: UIImage?
  let stopCamera: () -> Void

  var body: some View {
    Section("Glasses") {
      if !glasses.isRegistered {
        Button(glasses.isRegistering ? "Connecting…" : "Connect glasses in Meta AI") { glasses.register() }
          .disabled(glasses.isRegistering)
        Text("Needs Developer Mode on in the Meta AI app.")
          .font(.footnote)
          .foregroundStyle(.secondary)
      } else {
        LabeledContent("Glasses", value: glasses.hasActiveDevice ? "available" : "not detected")
        LabeledContent("Camera", value: glasses.streamStateText)
        if glasses.wantsStream {
          Button("Stop camera", role: .destructive, action: stopCamera)
        } else {
          Button("Start camera") { glasses.start() }
            .disabled(!glasses.canStart)
        }
        Button("Update glasses app in Meta AI") { glasses.openGlassesAppUpdate() }
          .font(.footnote)
        if glasses.isStreaming, let preview {
          Image(uiImage: preview)
            .resizable()
            .scaledToFit()
            .frame(maxHeight: 260)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .accessibilityLabel("Live glasses camera preview")
        }
      }
    }
  }
}

// MARK: - Hub

private struct HubSection: View {
  @Bindable var model: AppModel

  var body: some View {
    Section {
      TextField("ws://192.168.1.20:8765", text: $model.hubAddress)
        .keyboardType(.URL)
        .textInputAutocapitalization(.never)
        .autocorrectionDisabled()
        .disabled(model.hubEnabled)
        .accessibilityLabel("Hub address")
      Toggle(
        "Connect to hub",
        isOn: Binding(get: { model.hubEnabled }, set: { model.setHubEnabled($0) })
      )
      LabeledContent("Link", value: Self.describe(model.hubState))
      LabeledContent("Frames sent", value: "\(model.stats.sent)")
      LabeledContent("Camera / sent fps", value: String(format: "%.1f / %.1f", model.cameraFps, model.sentFps))
      LabeledContent("Encode / send ms", value: String(format: "%.1f / %.1f", model.encodeMs, model.sendMs))
      if model.stats.dropped + model.stats.failed > 0 {
        LabeledContent("Dropped / failed", value: "\(model.stats.dropped) / \(model.stats.failed)")
        LabeledContent("Rate limit / busy drops", value: "\(model.stats.throttled) / \(model.stats.busy)")
      }
    } header: {
      Text("Hub")
    } footer: {
      Text(
        "The hub prints its address when it starts. Phone and laptop must be on the same network (Wi-Fi or the phone's hotspot). \"retrying (timed out)\" usually means the network blocks phone-to-laptop traffic."
      )
    }
  }

  static func describe(_ state: HubClient.State) -> String {
    switch state {
    case .disconnected: return "off"
    case .connecting: return "connecting…"
    case .connected: return "connected"
    case .failed(let why): return "retrying (\(why))"
    }
  }
}

// MARK: - Belt

private struct BeltSection: View {
  let model: AppModel

  var body: some View {
    Section("Hub prediction") {
      TimelineView(.periodic(from: .now, by: 1)) { context in
        if let status = model.hubStatus, model.isStatusFresh(now: context.date) {
          VStack(alignment: .leading, spacing: 10) {
            Label(
              status.healthy ? "Hub input healthy" : "Not reliable: \(status.reason ?? "unknown")",
              systemImage: status.healthy ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
            )
            .foregroundStyle(status.healthy ? .green : .orange)
            LevelBars(levels: status.levels, mode: status.mode)
            HStack {
              Text("Hub belt transport: \(status.belt)")
              Spacer()
              if let ms = status.inferenceMs { Text("model \(Int(ms)) ms") }
            }
            .font(.footnote)
            .foregroundStyle(.secondary)
          }
          .accessibilityElement(children: .combine)
        } else {
          Text("No status from the hub.")
            .foregroundStyle(.secondary)
        }
      }
    }
  }
}

private struct LevelBars: View {
  let levels: [Int]
  let mode: String

  var body: some View {
    HStack(alignment: .bottom, spacing: 6) {
      ForEach(Array(levels.enumerated()), id: \.offset) { _, level in
        RoundedRectangle(cornerRadius: 3)
          .fill(mode == "fault" ? Color.orange : Color.red)
          .frame(height: mode == "fault" ? 8 : max(4, CGFloat(level) / 255 * 48))
          .frame(maxWidth: .infinity)
      }
    }
    .frame(height: 48, alignment: .bottom)
    .accessibilityLabel("Motor levels, left to right: \(levels.map(String.init).joined(separator: ", "))")
  }
}
