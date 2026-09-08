import AppKit
import Foundation

struct ProbeConfiguration: Equatable {
    let sessionID: String
    let logURL: URL

    static func parse(
        arguments: [String],
        defaultDirectory: URL? = nil,
        generatedSessionID: @autoclosure () -> String = UUID().uuidString
    ) throws -> ProbeConfiguration {
        var sessionID: String?
        var logPath: String?
        var index = arguments.first?.hasPrefix("--") == true ? 0 : 1

        while index < arguments.count {
            let argument = arguments[index]
            guard argument == "--session-id" || argument == "--log-path" else {
                throw ProbeConfigurationError.unknownArgument(argument)
            }
            guard index + 1 < arguments.count else {
                throw ProbeConfigurationError.missingValue(argument)
            }
            let value = arguments[index + 1]
            guard !value.isEmpty else {
                throw ProbeConfigurationError.missingValue(argument)
            }
            if argument == "--session-id" {
                sessionID = value
            } else {
                logPath = value
            }
            index += 2
        }

        let resolvedSessionID = sessionID ?? generatedSessionID()
        let directory = defaultDirectory ?? FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first!.appendingPathComponent("YutoriInputProbe/Sessions", isDirectory: true)
        let logURL = logPath.map { URL(fileURLWithPath: $0).standardizedFileURL }
            ?? directory.appendingPathComponent("\(resolvedSessionID).jsonl")
        return ProbeConfiguration(sessionID: resolvedSessionID, logURL: logURL)
    }
}

enum ProbeConfigurationError: LocalizedError, Equatable {
    case missingValue(String)
    case unknownArgument(String)

    var errorDescription: String? {
        switch self {
        case let .missingValue(argument):
            "Missing a value after \(argument)."
        case let .unknownArgument(argument):
            "Unknown argument: \(argument)"
        }
    }
}

struct ProbeStateSnapshot: Codable, Equatable {
    let appActive: Bool
    let appHidden: Bool
    let frontmostBundleID: String?
    let windowNumber: Int?
    let windowTitle: String?
    let windowKey: Bool
    let windowMain: Bool
    let windowVisible: Bool
    let windowMiniaturized: Bool
    let windowOccluded: Bool
    let firstResponder: String?
}

struct ProbeEvent: Codable, Equatable, Identifiable {
    let id: UUID
    let sessionID: String
    let sequence: Int
    let timestamp: String
    let monotonicNanoseconds: UInt64
    let category: String
    let name: String
    let details: [String: String]
    let state: ProbeStateSnapshot
}

enum ProbeEventEncoding {
    static func jsonLine(_ event: ProbeEvent) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        var data = try encoder.encode(event)
        data.append(0x0A)
        return data
    }
}

enum ProbeKeyFormatter {
    static func modifierNames(_ flags: NSEvent.ModifierFlags) -> [String] {
        var names: [String] = []
        if flags.contains(.control) { names.append("ctrl") }
        if flags.contains(.option) { names.append("option") }
        if flags.contains(.shift) { names.append("shift") }
        if flags.contains(.command) { names.append("cmd") }
        if flags.contains(.function) { names.append("fn") }
        if flags.contains(.capsLock) { names.append("capsLock") }
        return names
    }

    static func chord(charactersIgnoringModifiers: String?, flags: NSEvent.ModifierFlags) -> String {
        let modifiers = modifierNames(flags)
        let key = charactersIgnoringModifiers?.isEmpty == false
            ? charactersIgnoringModifiers!
            : "key"
        return (modifiers + [displayKey(key)]).joined(separator: "+")
    }

    static func displayKey(_ value: String) -> String {
        switch value {
        case "\r": "enter"
        case "\t": "tab"
        case "\u{1B}": "escape"
        case "\u{7F}": "backspace"
        case " ": "space"
        default:
            value.unicodeScalars.map { scalar in
                CharacterSet.controlCharacters.contains(scalar)
                    ? "U+\(String(scalar.value, radix: 16, uppercase: true))"
                    : String(scalar)
            }.joined()
        }
    }
}

enum ProbeEventTypeFormatter {
    static func name(_ type: NSEvent.EventType) -> String {
        switch type {
        case .leftMouseDown: "leftMouseDown"
        case .leftMouseUp: "leftMouseUp"
        case .rightMouseDown: "rightMouseDown"
        case .rightMouseUp: "rightMouseUp"
        case .otherMouseDown: "otherMouseDown"
        case .otherMouseUp: "otherMouseUp"
        case .mouseMoved: "mouseMoved"
        case .leftMouseDragged: "leftMouseDragged"
        case .rightMouseDragged: "rightMouseDragged"
        case .otherMouseDragged: "otherMouseDragged"
        case .keyDown: "keyDown"
        case .keyUp: "keyUp"
        case .flagsChanged: "flagsChanged"
        case .scrollWheel: "scrollWheel"
        default: "event-\(type.rawValue)"
        }
    }
}
