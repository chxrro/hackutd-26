import SwiftUI

/// Shows the current hub camera feed or starts a preview directly on the phone.
struct CameraPreviewView: View {
    @ObservedObject var bridge: BridgeModel
    @Environment(\.dynamicTypeSize) private var textSize
    @Environment(\.accessibilityDimFlashingLights) private var dimFlashingLights
    @State private var startedHere = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                TimelineView(.periodic(from: .now, by: 0.25)) { _ in
                    viewfinder
                }
                controls
            }
            .padding(20)
        }
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Glasses camera")
        .navigationBarTitleDisplayMode(.inline)
        .onDisappear {
            guard startedHere else { return }
            startedHere = false
            if bridge.showsLocalPreview { bridge.stop() }
        }
    }

    private var viewfinder: some View {
        let viewing = bridge.showsLocalPreview || bridge.activeDestination == .hub
        let fresh = bridge.lastFrame.map { $0.duration(to: .now) < .seconds(WearerStatus.frameTimeout) } ?? false
        let image = viewing && fresh && !dimFlashingLights ? bridge.previewImage : nil
        let label = sourceLabel
        let status = image != nil ? "Preview playing" : placeholder(viewing: viewing)
        let previewHeight: CGFloat = textSize.isAccessibilitySize ? 220 : 380
        let headerLayout = textSize.isAccessibilitySize
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
            : AnyLayout(HStackLayout(spacing: 8))

        return VStack(alignment: .leading, spacing: 12) {
            headerLayout {
                Label(label, systemImage: "eyeglasses")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
                if image != nil {
                    Text("Preview")
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(.white.opacity(0.14), in: Capsule())
                }
            }

            Group {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                        .frame(height: previewHeight)
                        .accessibilityHidden(true)
                } else {
                    VStack(spacing: 10) {
                        Image(systemName: "camera.viewfinder")
                            .font(.largeTitle)
                            .accessibilityHidden(true)
                        Text(status)
                            .font(.headline)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(16)
                    .frame(minHeight: previewHeight)
                }
            }
            .frame(maxWidth: .infinity)
            .background(.black)
            .clipShape(RoundedRectangle(cornerRadius: 18))
        }
        .padding(14)
        .foregroundStyle(.white)
        .background(Color(red: 0.08, green: 0.12, blue: 0.19), in: RoundedRectangle(cornerRadius: 26))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityValue(status)
        .accessibilityAddTraits(.isHeader)
        .onChange(of: status) { _, status in
            guard startedHere, bridge.activeDestination != .hub else { return }
            if !viewing || bridge.lastFrame != nil {
                var announcement = AttributedString("\(label). \(status)")
                announcement.accessibilitySpeechAnnouncementPriority = .low
                AccessibilityNotification.Announcement(announcement).post()
            }
        }
    }

    private var sourceLabel: String {
        if bridge.activeSource == .test { return "Simulated test pattern" }
        if bridge.usingMockGlasses { return "Simulated glasses" }
        return "Ray-Ban camera"
    }

    private func placeholder(viewing: Bool) -> String {
        if dimFlashingLights { return "Video preview hidden. Dim Flashing Lights is on." }
        if viewing { return bridge.lastFrame == nil ? "Waiting for camera frames" : "Camera feed stale" }
        if bridge.failedDestination == .screen || bridge.failedDestination == .hub {
            return bridge.failure ?? "Camera unavailable"
        }
        return "Camera is off"
    }

    @ViewBuilder
    private var controls: some View {
        let previewing = bridge.showsLocalPreview
        if bridge.activeDestination == .hub {
            Label("Camera in use by the belt session", systemImage: "dot.radiowaves.left.and.right")
                .font(.subheadline.weight(.semibold))
        } else {
            Button {
                if previewing { bridge.stop() }
                else { startPreview(.glasses) }
            } label: {
                Label(previewing ? "Stop preview" : "Start glasses preview",
                      systemImage: previewing ? "stop.fill" : "play.fill")
                    .frame(maxWidth: .infinity, minHeight: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(previewing ? .alertStop : .alertStart)
            .disabled(!previewing && (bridge.isSending || bridge.isSettingUp || dimFlashingLights))
            .accessibilityInputLabels(previewing
                ? ["Stop preview", "Stop camera"]
                : ["Start glasses preview", "Start camera"])
            .accessibilityHint(previewing ? "Turns off the camera preview." : "Shows the camera on this iPhone.")

            #if DEBUG
            if !previewing {
                Button("Preview test pattern") { startPreview(.test) }
                    .buttonStyle(.bordered)
                    .tint(.alertStart)
                    .disabled(bridge.isSending || bridge.isSettingUp || dimFlashingLights)
            }
            #endif

            Text(previewing || !bridge.isSending
                 ? "Video stays on this iPhone. Start belt alerts from the home screen."
                 : "Stop the active diagnostic in Setup to use this preview.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            if !bridge.isSending {
                Text("Pair the Ray-Bans and allow camera access in Setup.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func startPreview(_ source: FrameSource) {
        bridge.start(source, destination: .screen)
        startedHere = bridge.showsLocalPreview
    }
}
