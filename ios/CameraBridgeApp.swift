import SwiftUI

@main
struct CameraBridgeApp: App {
    @StateObject private var bridge = BridgeModel()
    @Environment(\.scenePhase) private var phase
    @State private var address = ""
    @State private var code = ""

    var body: some Scene {
        WindowGroup {
            NavigationStack {
                Group {
                    if bridge.showsLocalPreview {
                        localPreview
                    } else {
                        controls
                    }
                }
                .navigationTitle("Camera Bridge")
            }
            .onChange(of: phase) { _, newPhase in
                if newPhase == .background && bridge.isSending {
                    bridge.stop("Stopped when the app went into the background.")
                }
            }
            .onOpenURL(perform: bridge.handleCallback)
        }
    }

    private var controls: some View {
        Form {
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
                .disabled(bridge.isSending || bridge.isSettingUp)

                Group {
                    Button("Connect to Meta AI", action: bridge.register)
                    Button("Allow glasses camera access", action: bridge.requestCamera)
                    Button("Open glasses app update", action: bridge.openGlassesAppUpdate)
                    Button("Send glasses feed") {
                        bridge.start(.glasses, destination: .viewer(address: address, code: code))
                    }
                }
                .disabled(bridge.isSending || bridge.isSettingUp)

                if bridge.isSending {
                    Button("Stop sending", role: .destructive) { bridge.stop() }
                }
            }

            Section("On-screen preview") {
                Button("Preview test pattern") { bridge.start(.test, destination: .screen) }
                Button("Preview glasses feed") { bridge.start(.glasses, destination: .screen) }
            }
            .disabled(bridge.isSending || bridge.isSettingUp)

            Section("Status") {
                Text(bridge.sourceStatus)
                if bridge.isSending {
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
                if let image = bridge.previewImage {
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
