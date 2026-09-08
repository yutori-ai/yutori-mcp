@preconcurrency import AppKit
import Combine
import Foundation

@MainActor
final class EventRecorder: NSObject, ObservableObject {
    let configuration: ProbeConfiguration

    @Published private(set) var events: [ProbeEvent] = []
    @Published private(set) var latestState: ProbeStateSnapshot
    @Published private(set) var loggingError: String?
    @Published private(set) var armedScenario = "Freeform"
    @Published private(set) var keySinkText = ""
    @Published var clickCounts: [String: Int] = [:]

    private var sequence = 0
    private var installed = false
    private var localMonitor: Any?
    private let timestampFormatter = ISO8601DateFormatter()

    init(configuration: ProbeConfiguration) {
        self.configuration = configuration
        latestState = Self.captureState(window: nil)
        super.init()
    }

    func install() {
        guard !installed else { return }
        installed = true
        prepareLogFile(truncate: true)

        let eventMask: NSEvent.EventTypeMask = [
            .keyDown, .keyUp, .flagsChanged,
            .leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp,
            .otherMouseDown, .otherMouseUp, .leftMouseDragged, .rightMouseDragged,
            .otherMouseDragged, .scrollWheel,
        ]
        localMonitor = NSEvent.addLocalMonitorForEvents(matching: eventMask) { [weak self] event in
            MainActor.assumeIsolated {
                self?.record(event: event)
            }
            return event
        }

        let center = NotificationCenter.default
        let applicationNotifications: [Notification.Name] = [
            NSApplication.didBecomeActiveNotification,
            NSApplication.didResignActiveNotification,
            NSApplication.didHideNotification,
            NSApplication.didUnhideNotification,
        ]
        let windowNotifications: [Notification.Name] = [
            NSWindow.didBecomeKeyNotification,
            NSWindow.didResignKeyNotification,
            NSWindow.didBecomeMainNotification,
            NSWindow.didResignMainNotification,
            NSWindow.didMiniaturizeNotification,
            NSWindow.didDeminiaturizeNotification,
            NSWindow.didMoveNotification,
            NSWindow.didResizeNotification,
            NSWindow.willCloseNotification,
        ]
        for name in applicationNotifications + windowNotifications {
            center.addObserver(self, selector: #selector(handleLifecycleNotification(_:)), name: name, object: nil)
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(handleWorkspaceActivation(_:)),
            name: NSWorkspace.didActivateApplicationNotification,
            object: nil
        )

        record(
            category: "session",
            name: "ready",
            details: [
                "bundleID": Bundle.main.bundleIdentifier ?? "unknown",
                "logPath": configuration.logURL.path,
                "osVersion": ProcessInfo.processInfo.operatingSystemVersionString,
                "pid": "\(ProcessInfo.processInfo.processIdentifier)",
            ]
        )
    }

    func reset() {
        events.removeAll()
        keySinkText = ""
        clickCounts.removeAll()
        sequence = 0
        loggingError = nil
        prepareLogFile(truncate: true)
        record(category: "session", name: "reset")
    }

    func arm(_ scenario: String) {
        armedScenario = scenario
        record(category: "scenario", name: "armed", details: ["scenario": scenario])
    }

    func recordCommand(_ command: String, source: String = "menu") {
        record(category: "command", name: command, details: ["source": source])
    }

    func recordTextChange(target: String, value: String) {
        record(
            category: "text",
            name: "changed",
            details: ["target": target, "value": value, "length": "\(value.count)"]
        )
    }

    func appendToKeySink(_ value: String) {
        keySinkText += value
        recordTextChange(target: "keySink", value: keySinkText)
    }

    func deleteFromKeySink() {
        guard !keySinkText.isEmpty else { return }
        keySinkText.removeLast()
        recordTextChange(target: "keySink", value: keySinkText)
    }

    func recordClick(target: String) {
        clickCounts[target, default: 0] += 1
        record(
            category: "control",
            name: "activated",
            details: ["target": target, "count": "\(clickCounts[target, default: 0])"]
        )
    }

    func record(
        category: String,
        name: String,
        details: [String: String] = [:],
        window: NSWindow? = nil
    ) {
        sequence += 1
        let state = Self.captureState(window: window)
        latestState = state
        let event = ProbeEvent(
            id: UUID(),
            sessionID: configuration.sessionID,
            sequence: sequence,
            timestamp: timestampFormatter.string(from: Date()),
            monotonicNanoseconds: DispatchTime.now().uptimeNanoseconds,
            category: category,
            name: name,
            details: details,
            state: state
        )
        events.append(event)
        if events.count > 500 {
            events.removeFirst(events.count - 500)
        }
        appendToLog(event)
    }

    private func record(event: NSEvent) {
        let eventType = ProbeEventTypeFormatter.name(event.type)
        let recognizedCommand = Self.recognizedCommand(event)
        var details: [String: String] = [
            "eventType": eventType,
            "modifierFlags": ProbeKeyFormatter.modifierNames(event.modifierFlags).joined(separator: "+"),
            "windowNumber": "\(event.windowNumber)",
            "locationX": Self.decimal(event.locationInWindow.x),
            "locationY": Self.decimal(event.locationInWindow.y),
        ]

        switch event.type {
        case .keyDown, .keyUp, .flagsChanged:
            details["keyCode"] = "\(event.keyCode)"
            details["characters"] = event.characters ?? ""
            details["charactersIgnoringModifiers"] = event.charactersIgnoringModifiers ?? ""
            details["chord"] = ProbeKeyFormatter.chord(
                charactersIgnoringModifiers: event.charactersIgnoringModifiers,
                flags: event.modifierFlags
            )
            details["isRepeat"] = "\(event.isARepeat)"
        case .scrollWheel:
            details["deltaX"] = Self.decimal(event.scrollingDeltaX)
            details["deltaY"] = Self.decimal(event.scrollingDeltaY)
            details["precise"] = "\(event.hasPreciseScrollingDeltas)"
            details["phase"] = "\(event.phase.rawValue)"
            details["momentumPhase"] = "\(event.momentumPhase.rawValue)"
        default:
            details["buttonNumber"] = "\(event.buttonNumber)"
            details["clickCount"] = "\(event.clickCount)"
            details["pressure"] = Self.decimal(CGFloat(event.pressure))
        }

        record(category: "nsevent", name: eventType, details: details, window: event.window)
        if let recognizedCommand {
            recordCommand(recognizedCommand, source: "local-key-monitor")
        }
    }

    @objc private func handleLifecycleNotification(_ notification: Notification) {
        let window = notification.object as? NSWindow
        record(
            category: window == nil ? "application" : "window",
            name: notification.name.rawValue,
            details: window.map { ["title": $0.title, "windowNumber": "\($0.windowNumber)"] } ?? [:],
            window: window
        )
    }

    @objc private func handleWorkspaceActivation(_ notification: Notification) {
        let app = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
        record(
            category: "workspace",
            name: "frontmostChanged",
            details: [
                "bundleID": app?.bundleIdentifier ?? "unknown",
                "name": app?.localizedName ?? "unknown",
            ]
        )
    }

    private func prepareLogFile(truncate: Bool) {
        do {
            try FileManager.default.createDirectory(
                at: configuration.logURL.deletingLastPathComponent(),
                withIntermediateDirectories: true
            )
            if truncate || !FileManager.default.fileExists(atPath: configuration.logURL.path) {
                try Data().write(to: configuration.logURL, options: .atomic)
            }
        } catch {
            loggingError = error.localizedDescription
        }
    }

    private func appendToLog(_ event: ProbeEvent) {
        do {
            let data = try ProbeEventEncoding.jsonLine(event)
            let handle = try FileHandle(forWritingTo: configuration.logURL)
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
            try handle.close()
        } catch {
            loggingError = error.localizedDescription
        }
    }

    private static func captureState(window: NSWindow?) -> ProbeStateSnapshot {
        let resolvedWindow = window ?? NSApp.keyWindow ?? NSApp.mainWindow ?? NSApp.windows.first
        let responder = resolvedWindow?.firstResponder.map { String(describing: type(of: $0)) }
        return ProbeStateSnapshot(
            appActive: NSApp.isActive,
            appHidden: NSApp.isHidden,
            frontmostBundleID: NSWorkspace.shared.frontmostApplication?.bundleIdentifier,
            windowNumber: resolvedWindow?.windowNumber,
            windowTitle: resolvedWindow?.title,
            windowKey: resolvedWindow?.isKeyWindow ?? false,
            windowMain: resolvedWindow?.isMainWindow ?? false,
            windowVisible: resolvedWindow?.isVisible ?? false,
            windowMiniaturized: resolvedWindow?.isMiniaturized ?? false,
            windowOccluded: resolvedWindow?.occlusionState.contains(.visible) == false,
            firstResponder: responder
        )
    }

    private static func decimal(_ value: CGFloat) -> String {
        String(format: "%.2f", Double(value))
    }

    private static func recognizedCommand(_ event: NSEvent) -> String? {
        guard event.type == .keyDown, let key = event.charactersIgnoringModifiers?.lowercased() else {
            return nil
        }
        let flags = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if key == "k", flags == [.command] { return "cmd+k" }
        if key == "u", flags == [.command, .shift] { return "cmd+shift+u" }
        if key == "k", flags == [.control] { return "ctrl+k" }
        if key == "k", flags == [.option] { return "option+k" }
        return nil
    }
}
