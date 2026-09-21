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
    private var secondaryWindow: NSWindow?
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

        if configuration.secondaryWindow {
            openSecondaryWindow()
        }
        record(
            category: "session",
            name: "ready",
            details: [
                "bundleID": Bundle.main.bundleIdentifier ?? "unknown",
                "logPath": configuration.logURL.path,
                "osVersion": ProcessInfo.processInfo.operatingSystemVersionString,
                "pid": "\(ProcessInfo.processInfo.processIdentifier)",
                "secondaryWindow": "\(configuration.secondaryWindow)",
            ]
        )
        recordWindowInventory(reason: "ready")
    }

    /// A sibling keyboard destination: an untitled, visible window ordered behind the content
    /// window, never made key. Chrome and Safari Technology Preview own such windows, and the
    /// driver refuses pid-addressed keystrokes whenever one exists.
    private func openSecondaryWindow() {
        let window = NSWindow(
            contentRect: NSRect(x: 60, y: 60, width: 360, height: 200),
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = ""
        window.isReleasedWhenClosed = false
        let label = NSTextField(wrappingLabelWithString: "Secondary window: a sibling keyboard destination in the same process.")
        label.frame = NSRect(x: 16, y: 16, width: 328, height: 168)
        label.isSelectable = false
        window.contentView?.addSubview(label)
        window.orderBack(nil)
        secondaryWindow = window
        record(
            category: "windows",
            name: "secondaryOpened",
            details: ["windowNumber": "\(window.windowNumber)"],
            window: window
        )
    }

    /// Every window's key/main flags plus the app-level key and main windows. AppKit resigns key
    /// on deactivation, so this is the ground truth for what AX `AXFocused`/`AXMain` can report
    /// while the app is driven in the background.
    func recordWindowInventory(reason: String) {
        var details: [String: String] = [
            "reason": reason,
            "keyWindow": NSApp.keyWindow.map { "\($0.windowNumber)" } ?? "none",
            "mainWindow": NSApp.mainWindow.map { "\($0.windowNumber)" } ?? "none",
        ]
        for window in NSApp.windows where window.isVisible || window.isMiniaturized {
            let title = window.title.isEmpty ? "(untitled)" : window.title
            details["window.\(window.windowNumber)"] =
                "title=\(title) key=\(window.isKeyWindow) main=\(window.isMainWindow) "
                + "visible=\(window.isVisible) miniaturized=\(window.isMiniaturized)"
        }
        record(category: "windows", name: "inventory", details: details)
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

    func recordSubmit(target: String, value: String) {
        record(
            category: "control",
            name: "submitted",
            details: ["target": target, "value": value, "length": "\(value.count)"]
        )
    }

    func recordWebEvent(name: String, details: [String: String]) {
        record(category: "web", name: name, details: details)
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
            "locationX": ProbeNumberFormatter.decimal(event.locationInWindow.x),
            "locationY": ProbeNumberFormatter.decimal(event.locationInWindow.y),
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
            details["deltaX"] = ProbeNumberFormatter.decimal(event.scrollingDeltaX)
            details["deltaY"] = ProbeNumberFormatter.decimal(event.scrollingDeltaY)
            details["precise"] = "\(event.hasPreciseScrollingDeltas)"
            details["phase"] = "\(event.phase.rawValue)"
            details["momentumPhase"] = "\(event.momentumPhase.rawValue)"
        default:
            details["buttonNumber"] = "\(event.buttonNumber)"
            details["clickCount"] = "\(event.clickCount)"
            details["pressure"] = ProbeNumberFormatter.decimal(CGFloat(event.pressure))
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
        if window == nil {
            recordWindowInventory(reason: notification.name.rawValue)
        }
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
