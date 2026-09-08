// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "YutoriInputProbe",
    platforms: [
        .macOS(.v15),
    ],
    products: [
        .executable(name: "YutoriInputProbe", targets: ["YutoriInputProbe"]),
    ],
    targets: [
        .executableTarget(name: "YutoriInputProbe"),
        .testTarget(
            name: "YutoriInputProbeTests",
            dependencies: ["YutoriInputProbe"]
        ),
    ]
)
