//
// ContentView.swift
//
// One screen, three sections that mirror the system: glasses, hub, belt.
//

import SwiftUI

struct ContentView: View {
  @Bindable var model: AppModel

  var body: some View {
    NavigationStack {
      Form {
        GlassesSection(glasses: model.glasses, preview: model.preview)
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

// MARK: - Glasses

private struct GlassesSection: View {
  let glasses: GlassesController
  let preview: UIImage?

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
          Button("Stop camera", role: .destructive) { glasses.stop() }
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
      if model.stats.dropped + model.stats.failed > 0 {
        LabeledContent("Dropped / failed", value: "\(model.stats.dropped) / \(model.stats.failed)")
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
    Section("Belt") {
      TimelineView(.periodic(from: .now, by: 1)) { context in
        if let status = model.hubStatus, model.isStatusFresh(now: context.date) {
          VStack(alignment: .leading, spacing: 10) {
            Label(
              status.healthy ? "Alerts active" : "Not reliable: \(status.reason ?? "unknown")",
              systemImage: status.healthy ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
            )
            .foregroundStyle(status.healthy ? .green : .orange)
            LevelBars(levels: status.levels, mode: status.mode)
            HStack {
              Text("belt: \(status.belt)")
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
