import SwiftUI

/// Setup and demo screen for a sighted helper: pairing, glasses setup, hub
/// settings, recording consent, live preview and connection diagnostics.
struct HelperView: View {
    @ObservedObject var model: AppModel
    @ObservedObject var hub: HubSession
    @ObservedObject var bridge: BridgeModel
    #if DEBUG
    @ObservedObject var mock: MockGlasses
    #endif

    @State private var address = ""
    @State private var code = ""
    @State private var pairingError: String?
    @State private var askRecordingConsent = false
    @FocusState private var editingPairing: Bool

    init(model: AppModel) {
        self.model = model
        hub = model.hub
        bridge = model.bridge
        #if DEBUG
        mock = model.bridge.mockGlasses
        #endif
    }

    var body: some View {
        Form {
            pairingSection
            diagnosticsSection
            #if DEBUG
            developmentSection
            #endif
            glassesSection
            previewSection
            settingsSection
            recordingSection
            wearerSection
            Section {
                NavigationLink("Advanced: diagnostic viewer") { AdvancedView(bridge: bridge, hubRunning: model.isRunning) }
            } footer: {
                Text("Sends frames to desktop/viewer.py over HTTP, or previews them on this screen. Not connected to the hub or the belt.")
            }
        }
        .navigationTitle("Setup")
        .scrollDismissesKeyboard(.immediately)
        .onAppear {
            let saved = PairingStore.load()
            address = saved.address
            code = saved.code
        }
        .alert("Record this session?", isPresented: $askRecordingConsent) {
            Button("Everyone agreed. Record.") { model.setRecording(true) }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The hub will save camera video from this session on the laptop. Only record if the wearer and everyone likely to be filmed have agreed. Never commit recordings to the repository.")
        }
    }

    // MARK: Sections

    private var pairingSection: some View {
        Section {
            TextField("Hub address, e.g. 192.168.1.20:8765", text: $address)
                .textContentType(.URL)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($editingPairing)
            SecureField("Control code from the hub terminal", text: $code)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($editingPairing)
            Button("Save and connect") {
                editingPairing = false
                pairingError = model.savePairing(address: address, code: code)
            }
            .accessibilityHint("Reconnects to the hub. During a session the link drops for a moment, and the wearer hears a warning until it is back.")
            if model.pairing != nil {
                Button("Forget hub", role: .destructive) {
                    model.forgetPairing()
                    address = ""
                    code = ""
                }
            }
            if let pairingError {
                warningLabel(pairingError)
            }
            if model.codeNotSaved {
                warningLabel("The control code couldn’t be saved on this phone. Enter it again after the app restarts.")
            }
        } header: {
            Text("Hub")
        } footer: {
            Text("Leave the control code empty to send frames only. Pause, settings, belt test and recording need the code. The code changes every time the hub restarts.")
        }
    }

    private var diagnosticsSection: some View {
        Section("Connection") {
            TimelineView(.periodic(from: .now, by: 0.5)) { _ in
                VStack(alignment: .leading, spacing: 10) {
                    row("Hub link", linkText, warn: hub.link != .connected)
                    let age = hub.statusAge()
                    row("Last status", age.map { String(format: "%.1f s ago", $0) } ?? "None",
                        warn: age.map { $0 > WearerStatus.statusTimeout } ?? true)
                    if let status = hub.status {
                        row("Hub health", status.healthy ? "Healthy" : "Not healthy: \(status.reason ?? "no reason given")",
                            warn: !status.healthy)
                        row("Alerts", status.isPaused ? "Paused" : "Not paused", warn: status.isPaused)
                        row("Belt", status.belt, warn: status.belt != "connected")
                        row("Belt mode", status.mode, warn: status.mode != "levels")
                        row("Belt levels", status.levels.map(String.init).joined(separator: " "), warn: false)
                        if let ms = status.inferenceMs { row("Inference", String(format: "%.0f ms", ms), warn: false) }
                        if let ms = status.frameAgeMs { row("Hub frame age", String(format: "%.0f ms", ms), warn: ms > 1000) }
                    }
                    let d = model.diagnostics
                    row("Camera frames", String(format: "%.1f per s", d.framesInPerSecond), warn: model.isRunning && d.framesInPerSecond == 0)
                    row("Sent to hub", String(format: "%.1f per s", d.framesSentPerSecond), warn: model.isRunning && d.framesSentPerSecond == 0)
                    row("Dropped / failed sends", "\(d.dropped) / \(d.failed)", warn: false)
                    row("Control code", controlText, warn: hub.controlPairing != .paired)
                    row("Wearer screen", model.condition.sentence, warn: model.condition.tone == .warning)
                    if !model.recentAnnouncements.isEmpty {
                        Text("Recent announcements (newest first)")
                        ForEach(Array(model.recentAnnouncements.enumerated()), id: \.offset) { _, text in
                            Text("“\(text)”").font(.callout).foregroundStyle(.secondary)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            Button("Verify control code", action: model.verifyControlCode)
                .disabled(!model.canVerifyControlCode)
        }
    }

    private var glassesSection: some View {
        Section {
            stepRow("1. Connect to Meta AI", status: registrationStep, action: bridge.register)
            stepRow("2. Allow glasses camera access", status: cameraStep, action: bridge.requestCamera)
            stepRow("3. Open glasses app update", status: updateStep, action: bridge.openGlassesAppUpdate)
            row("Glasses", bridge.glassesSetup.map { $0.deviceConnected ? "Connected" : "Not connected" } ?? "Not checked",
                warn: bridge.glassesSetup?.deviceConnected != true)
            Button("Check glasses status", action: bridge.refreshGlassesSetup)
                .disabled(bridge.isSettingUp)
            Picker("Camera source", selection: $model.source) {
                Text("Ray-Ban glasses").tag(FrameSource.glasses)
                Text("Test pattern (simulated)").tag(FrameSource.test)
            }
            .disabled(model.isRunning)
            Text(bridge.sourceStatus)
                .font(.footnote)
                .foregroundStyle(.secondary)
        } header: {
            Text(bridge.usingMockGlasses ? "Glasses: MOCK (simulated)" : "Glasses")
        } footer: {
            Text("Pair the glasses in Meta AI and enable Developer Mode first. Wear the glasses when starting: some glasses refuse to start a camera session while folded.")
        }
    }

    #if DEBUG
    /// Debug builds only: Meta's MockDeviceKit glasses, to test the real path without the Ray-Bans.
    private var developmentSection: some View {
        Section {
            Toggle("Use mock glasses", isOn: Binding(get: { mock.isOn }, set: { bridge.setMockGlasses($0) }))
                .disabled(bridge.isSending)
            if !mock.feedDescription.isEmpty {
                row("Feed", mock.feedDescription, warn: false)
            }
            Toggle("Hold frames to hub (test fault)", isOn: $bridge.holdFramesToHub)
        } header: {
            Text("Development: MOCK (simulated)")
        } footer: {
            Text("Debug builds only. Meta’s simulated glasses: frames are not from the Ray-Bans, and the wearer screen says so. Feed: Documents/\(MockGlasses.feedFileName) (HEVC) if present, otherwise the phone camera. “Hold frames” keeps the camera running but stops sending to the hub, so the hub reports a fault. Keep mock runs out of any measurement.")
        }
    }
    #endif

    @ViewBuilder
    private var previewSection: some View {
        if model.isRunning {
            Section("Live preview") {
                if let image = bridge.previewImage {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(maxHeight: 280)
                        .frame(maxWidth: .infinity)
                        .accessibilityHidden(true)
                } else {
                    warningLabel("Waiting for the first camera frame.")
                }
                Text(bridge.activeSource == .test ? "Test pattern · simulated source"
                    : bridge.usingMockGlasses ? "MOCK glasses · simulated source" : "Ray-Ban camera frames sent to the hub")
                    .font(.footnote)
            }
        }
    }

    private var settingsSection: some View {
        Section {
            Picker("Person / object labels", selection: classifierBinding) {
                Text("Not reported").tag(Classifier?.none)
                Text("Local detector").tag(Classifier?.some(.local))
                Text("Cloud model").tag(Classifier?.some(.nim))
            }
            Picker("Alert range", selection: alertRangeBinding) {
                Text("Not reported").tag(AlertRange?.none)
                Text("Near").tag(AlertRange?.some(.near))
                Text("Normal").tag(AlertRange?.some(.normal))
                Text("Far").tag(AlertRange?.some(.far))
            }
            Button("Test belt", action: model.beltTest)
                .disabled(model.isPaused)
                .accessibilityHint("The hub plays a short sweep across the motors.")
            if !hub.pending.isEmpty {
                HStack {
                    ProgressView()
                    Text("Waiting for the hub…")
                }
            } else if let last = hub.lastResult {
                if last.result == .ok {
                    Label("\(describe(last.action)): \(last.result.message)", systemImage: "checkmark.circle")
                } else {
                    warningLabel("\(describe(last.action)): \(last.result.message)")
                }
            }
        } header: {
            Text("Hub settings")
        } footer: {
            Text("The pickers show what the hub reports, not what was tapped. Needs the control code.")
        }
        .disabled(!hub.hasControlCode || hub.link != .connected)
    }

    private var recordingSection: some View {
        Section {
            Toggle("Record session on the hub", isOn: Binding(
                get: { hub.settings?.record ?? false },
                set: { on in
                    if on { askRecordingConsent = true } else { model.setRecording(false) }
                }))
        } header: {
            Text("Recording")
        } footer: {
            Text("Off unless you turn it on. You'll be asked to confirm consent first.")
        }
        .disabled(!hub.hasControlCode || hub.link != .connected)
    }

    private var wearerSection: some View {
        Section {
            Toggle("Say “person” or “object” and its direction", isOn: $model.speakObstacleKinds)
            Toggle("Phone haptics for state changes", isOn: $model.phoneHaptics)
        } header: {
            Text("Wearer speech")
        } footer: {
            Text("Speech names the strongest current belt alert, like “Person ahead”. It is limited to once every few seconds and never says where to walk or that the path is clear.")
        }
    }

    // MARK: Helpers

    private var classifierBinding: Binding<Classifier?> {
        Binding(
            get: { hub.settings?.classifier.flatMap(Classifier.init(rawValue:)) },
            set: { if let value = $0 { model.setClassifier(value) } })
    }

    private var alertRangeBinding: Binding<AlertRange?> {
        Binding(
            get: { hub.settings?.alertRange.flatMap(AlertRange.init(rawValue:)) },
            set: { if let value = $0 { model.setAlertRange(value) } })
    }

    private var linkText: String {
        guard model.pairing != nil else { return "Not paired" }
        switch hub.link {
        case .connected: return "Connected"
        case .connecting: return "Connecting…"
        case .disconnected: return "Not connected"
        case .failed(let why): return "Not connected: \(why)"
        }
    }

    private var controlText: String {
        switch hub.controlPairing {
        case .noCode: return "Not entered (frames only)"
        case .unverified: return "Not verified yet"
        case .paired: return "Accepted by the hub"
        case .rejected: return "Rejected: check the code in the hub terminal"
        case .noReply: return "No reply: the hub may not support control yet"
        }
    }

    private var registrationStep: (String, Bool) {
        guard let setup = bridge.glassesSetup else { return ("Not checked", false) }
        return setup.registered ? ("Done", true) : ("Not connected", false)
    }

    private var cameraStep: (String, Bool) {
        switch bridge.glassesSetup?.cameraGranted {
        case true?: return ("Allowed", true)
        case false?: return ("Not allowed", false)
        case nil: return ("Not checked", false)
        }
    }

    private var updateStep: (String, Bool) {
        bridge.glassesNeedUpdate ? ("Needed", false) : ("Only if the glasses ask", true)
    }

    private func describe(_ action: ControlAction) -> String {
        switch action {
        case .get: return "Check code"
        case .pause: return "Pause"
        case .resume: return "Resume"
        case .beltTest: return "Belt test"
        case .endSession: return "Stop (end session)"
        case .set(let change):
            if change.record != nil { return "Recording" }
            if change.classifier != nil { return "Labels" }
            return "Alert range"
        }
    }

    private func stepRow(_ title: String, status: (String, Bool), action: @escaping () -> Void) -> some View {
        HStack {
            Button(title, action: action)
                .disabled(bridge.isSending || bridge.isSettingUp)
            Spacer()
            Label(status.0, systemImage: status.1 ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(status.1 ? Color.green : Color.orange)
                .font(.callout)
        }
        .accessibilityElement(children: .combine)
        .accessibilityValue(status.0)
    }

    private func row(_ title: String, _ value: String, warn: Bool) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title)
            Spacer()
            if warn {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                    .accessibilityLabel("Warning")
            }
            Text(value)
                .multilineTextAlignment(.trailing)
                .foregroundStyle(warn ? .primary : .secondary)
        }
        .accessibilityElement(children: .combine)
    }

    private func warningLabel(_ text: String) -> some View {
        Label(text, systemImage: "exclamationmark.triangle.fill")
            .foregroundStyle(.primary)
            .symbolRenderingMode(.multicolor)
    }
}
