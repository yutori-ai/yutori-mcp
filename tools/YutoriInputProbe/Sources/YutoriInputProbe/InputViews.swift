@preconcurrency import AppKit
import SwiftUI

@MainActor
final class GeometryReportingView: NSView {
    weak var recorder: EventRecorder?
    var target = ""
    private var lastReportedFrame: CGRect?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        reportGeometrySoon()
    }

    override func layout() {
        super.layout()
        reportGeometrySoon()
    }

    private func reportGeometrySoon() {
        DispatchQueue.main.async { [weak self] in
            self?.reportGeometry()
        }
    }

    private func reportGeometry() {
        guard let window, let recorder, !bounds.isEmpty else { return }
        let rectInWindow = convert(bounds, to: nil)
        let screenRect = window.convertToScreen(rectInWindow)
        let windowFrame = window.frame
        let topLeftRect = CGRect(
            x: screenRect.minX - windowFrame.minX,
            y: windowFrame.maxY - screenRect.maxY,
            width: screenRect.width,
            height: screenRect.height
        )
        guard lastReportedFrame != topLeftRect else { return }
        lastReportedFrame = topLeftRect
        recorder.record(
            category: "layout",
            name: "targetFrame",
            details: [
                "target": target,
                "windowX": Self.decimal(topLeftRect.minX),
                "windowY": Self.decimal(topLeftRect.minY),
                "width": Self.decimal(topLeftRect.width),
                "height": Self.decimal(topLeftRect.height),
                "windowWidth": Self.decimal(windowFrame.width),
                "windowHeight": Self.decimal(windowFrame.height),
                "screenX": Self.decimal(screenRect.minX),
                "screenY": Self.decimal(screenRect.minY),
            ],
            window: window
        )
    }

    private static func decimal(_ value: CGFloat) -> String {
        String(format: "%.2f", Double(value))
    }
}

struct TargetGeometryReporter: NSViewRepresentable {
    let target: String
    let recorder: EventRecorder

    func makeNSView(context: Context) -> GeometryReportingView {
        let view = GeometryReportingView()
        view.target = target
        view.recorder = recorder
        return view
    }

    func updateNSView(_ view: GeometryReportingView, context: Context) {
        view.target = target
        view.recorder = recorder
    }
}

extension View {
    func reportProbeGeometry(_ target: String, recorder: EventRecorder) -> some View {
        background(TargetGeometryReporter(target: target, recorder: recorder))
    }
}

@MainActor
final class RawKeySinkView: NSView {
    weak var recorder: EventRecorder?

    override var acceptsFirstResponder: Bool { true }
    override var isFlipped: Bool { true }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        setAccessibilityElement(true)
        setAccessibilityRole(.group)
        setAccessibilityLabel("Raw keyboard input sink")
        setAccessibilityIdentifier("probe.keySink")
        DispatchQueue.main.async { [weak self] in
            guard let self, let window = self.window else { return }
            window.makeFirstResponder(self)
            self.recorder?.record(category: "focus", name: "keySinkFirstResponder", window: window)
        }
    }

    override func becomeFirstResponder() -> Bool {
        recorder?.record(category: "focus", name: "keySinkBecameFirstResponder", window: window)
        needsDisplay = true
        return true
    }

    override func resignFirstResponder() -> Bool {
        recorder?.record(category: "focus", name: "keySinkResignedFirstResponder", window: window)
        needsDisplay = true
        return true
    }

    override func keyDown(with event: NSEvent) {
        let chord = ProbeKeyFormatter.chord(
            charactersIgnoringModifiers: event.charactersIgnoringModifiers,
            flags: event.modifierFlags
        )
        recorder?.record(
            category: "responder",
            name: "keyDown",
            details: ["chord": chord, "keyCode": "\(event.keyCode)"],
            window: window
        )

        if event.keyCode == 51 || event.keyCode == 117 {
            recorder?.deleteFromKeySink()
        } else if event.modifierFlags.intersection([.command, .control]).isEmpty,
                  let characters = event.characters,
                  !characters.isEmpty,
                  !characters.unicodeScalars.allSatisfy(CharacterSet.controlCharacters.contains) {
            recorder?.appendToKeySink(characters)
        }
        needsDisplay = true
    }

    override func draw(_ dirtyRect: NSRect) {
        NSColor.textBackgroundColor.setFill()
        dirtyRect.fill()
        let value = recorder?.keySinkText.isEmpty == false
            ? recorder!.keySinkText
            : "Waiting for type/key_press…"
        let attributes: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 14, weight: .regular),
            .foregroundColor: recorder?.keySinkText.isEmpty == false ? NSColor.labelColor : NSColor.secondaryLabelColor,
        ]
        NSString(string: value).draw(in: bounds.insetBy(dx: 8, dy: 8), withAttributes: attributes)
    }
}

struct ProbeKeySink: NSViewRepresentable {
    let recorder: EventRecorder

    func makeNSView(context: Context) -> RawKeySinkView {
        let view = RawKeySinkView()
        view.recorder = recorder
        view.wantsLayer = true
        view.layer?.borderColor = NSColor.separatorColor.cgColor
        view.layer?.borderWidth = 1
        view.layer?.cornerRadius = 6
        return view
    }

    func updateNSView(_ view: RawKeySinkView, context: Context) {
        view.recorder = recorder
        view.needsDisplay = true
    }
}

struct DragProbe: View {
    let recorder: EventRecorder
    @State private var offset: CGSize = .zero

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(nsColor: .controlBackgroundColor))
                .overlay {
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(.secondary.opacity(0.4), style: StrokeStyle(lineWidth: 1, dash: [5]))
                }
            Text("Drag token here")
                .foregroundStyle(.secondary)
            Circle()
                .fill(.indigo)
                .frame(width: 42, height: 42)
                .overlay(Text("↔").foregroundStyle(.white))
                .offset(offset)
                .gesture(
                    DragGesture()
                        .onChanged { value in
                            offset = value.translation
                        }
                        .onEnded { value in
                            recorder.record(
                                category: "gesture",
                                name: "dragEnded",
                                details: [
                                    "translationX": String(format: "%.2f", value.translation.width),
                                    "translationY": String(format: "%.2f", value.translation.height),
                                ]
                            )
                        }
                )
                .accessibilityIdentifier("probe.drag.token")
                .accessibilityLabel("Draggable token")
        }
        .frame(height: 105)
        .accessibilityIdentifier("probe.drag.area")
    }
}
