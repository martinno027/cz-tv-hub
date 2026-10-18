// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "CTCore",
    platforms: [.tvOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "CTCore", targets: ["CTCore"]),
    ],
    targets: [
        .target(name: "CTCore"),
        // Fixtures are read from spec/fixtures (found upward from the tests, see Fixture in Support.swift).
        .testTarget(name: "CTCoreTests", dependencies: ["CTCore"]),
    ]
)
