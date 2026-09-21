@preconcurrency import AppKit
import SwiftUI
@preconcurrency import WebKit

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
                "windowX": ProbeNumberFormatter.decimal(topLeftRect.minX),
                "windowY": ProbeNumberFormatter.decimal(topLeftRect.minY),
                "width": ProbeNumberFormatter.decimal(topLeftRect.width),
                "height": ProbeNumberFormatter.decimal(topLeftRect.height),
                "windowWidth": ProbeNumberFormatter.decimal(windowFrame.width),
                "windowHeight": ProbeNumberFormatter.decimal(windowFrame.height),
                "screenX": ProbeNumberFormatter.decimal(screenRect.minX),
                "screenY": ProbeNumberFormatter.decimal(screenRect.minY),
            ],
            window: window
        )
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
                                    "translationX": ProbeNumberFormatter.decimal(value.translation.width),
                                    "translationY": ProbeNumberFormatter.decimal(value.translation.height),
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

/// One `<input>` inside a `<form>`, filling the whole web view so any pointer inside the
/// reported frame lands in the field. The page reports key, input, focus, and submit events
/// back through a script message handler; `submit` is the web analogue of Enter landing.
@MainActor
final class WebFormBridge: NSObject, WKScriptMessageHandler {
    weak var recorder: EventRecorder?

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any] else { return }
        var details: [String: String] = [:]
        for (key, value) in body {
            details[key] = String(describing: value)
        }
        let name = details.removeValue(forKey: "name") ?? "message"
        recorder?.recordWebEvent(name: name, details: details)
    }
}

struct ProbeWebForm: NSViewRepresentable {
    let recorder: EventRecorder

    static let html = """
    <!doctype html>
    <html><head><meta charset="utf-8"></head>
    <body style="margin:0;background:#fff">
    <form id="f" style="margin:0;height:100vh;display:flex">
      <input id="q" name="q" autocomplete="off" autocapitalize="off" spellcheck="false"
             placeholder="web form: type, then Enter submits"
             style="flex:1;font:14px Menlo,monospace;padding:0 8px;border:0;outline:1px solid #c8c8c8">
    </form>
    <script>
    const post = (name, extra) => window.webkit.messageHandlers.probe.postMessage(Object.assign({name}, extra || {}));
    const q = document.getElementById('q');
    const f = document.getElementById('f');
    for (const type of ['keydown', 'keyup', 'keypress']) {
      q.addEventListener(type, (e) => post(type, {key: e.key, code: e.code, keyCode: String(e.keyCode)}));
    }
    q.addEventListener('input', () => post('input', {value: q.value}));
    document.addEventListener('keydown', (e) => post('documentKeydown', {
      key: e.key, code: e.code, keyCode: String(e.keyCode),
      activeElement: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : 'none',
    }));
    q.addEventListener('focus', () => post('focus'));
    q.addEventListener('blur', () => post('blur'));
    f.addEventListener('submit', (e) => { e.preventDefault(); post('submit', {value: q.value}); });
    post('ready');
    </script>
    </body></html>
    """

    func makeCoordinator() -> WebFormBridge {
        let bridge = WebFormBridge()
        bridge.recorder = recorder
        return bridge
    }

    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(context.coordinator, name: "probe")
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.setAccessibilityIdentifier("probe.webForm")
        webView.loadHTMLString(Self.html, baseURL: nil)
        return webView
    }

    func updateNSView(_ view: WKWebView, context: Context) {
        context.coordinator.recorder = recorder
    }
}
