import SwiftUI

/// Ethan's original Camera Bridge diagnostic: send JPEGs to desktop/viewer.py
/// over HTTP, or preview them on this screen (for Device Hub mirroring).
/// Separate from the hub and the belt.
struct AdvancedView: View {
    @ObservedObject var bridge: BridgeModel
    @Environment(\.accessibilityDimFlashingLights) private var dimFlashingLights
    let hubRunning: Bool
    @State private var address = ""
    @State private var code = ""

    var body: some View {
        Group {
            if bridge.showsLocalPreview {
                localPreview
            } else {
                controls
            }
        }
        .navigationTitle("Diagnostic viewer")
    }

    private var busy: Bool { bridge.isSending || bridge.isSettingUp || hubRunning }

    private var controls: some View {
        Form {
            if hubRunning {
                Section {
                    Label("Stop belt alerts on the main screen before using the diagnostic viewer.",
                          systemImage: "exclamationmark.triangle.fill")
                }
            }

            Section("Computer viewer") {
                TextField("http://computer.local:8765", text: $address)
                    .textContentType(.URL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField("Pairing code from terminal", text: $code)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }
            .disabled(bridge.isSending)

            Section("Source") {
                Button("Send test pattern") {
                    bridge.start(.test, destination: .viewer(address: address, code: code))
                }
                .disabled(busy)

                Button("Send glasses feed") {
                    bridge.start(.glasses, destination: .viewer(address: address, code: code))
                }
                .disabled(busy)

                if bridge.isSending, bridge.activeDestination == .viewer {
                    Button("Stop sending", role: .destructive) { bridge.stop() }
                }
            }

            Section {
                Button("Preview test pattern") { bridge.start(.test, destination: .screen) }
                    .disabled(dimFlashingLights)
                Button("Preview glasses feed") { bridge.start(.glasses, destination: .screen) }
                    .disabled(dimFlashingLights)
            } header: {
                Text("On-screen preview")
            } footer: {
                if dimFlashingLights { Text("Video preview hidden. Dim Flashing Lights is on.") }
            }
            .disabled(busy)

            Section("Status") {
                Text(bridge.sourceStatus)
                if bridge.isSending, bridge.activeDestination == .viewer {
                    Text(bridge.viewerStatus)
                }
            }
        }
    }

    private var localPreview: some View {
        VStack(spacing: 16) {
            Text(bridge.sourceStatus)
                .font(.headline)
            ZStack {
                Color.black
                if dimFlashingLights {
                    Text("Video preview hidden. Dim Flashing Lights is on.")
                        .foregroundStyle(.white)
                        .padding()
                } else if let image = bridge.previewImage {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .accessibilityHidden(true)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            Button("Stop preview", role: .destructive) { bridge.stop() }
                .buttonStyle(.bordered)
        }
        .padding()
    }
}
