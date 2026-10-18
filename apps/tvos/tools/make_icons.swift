// Generates the placeholder tvOS app icon (two parallax layers) and Top Shelf images.
// Neutral artwork on purpose: no Česká televize logo or branding.
// Usage (from apps/tvos): swift tools/make_icons.swift
import AppKit
import CoreGraphics
import Foundation

let catalog = URL(fileURLWithPath: "Televize/Resources/Assets.xcassets")
let brand = catalog.appendingPathComponent("App Icon & Top Shelf Image.brandassets")
let info = #""info" : { "author" : "xcode", "version" : 1 }"#

func write(_ text: String, to url: URL) throws {
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try text.write(to: url, atomically: true, encoding: .utf8)
}

func png(width: Int, height: Int, to url: URL, draw: (CGContext, CGFloat, CGFloat) -> Void) throws {
    let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpace(name: CGColorSpace.sRGB)!,
                            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    draw(context, CGFloat(width), CGFloat(height))
    let rep = NSBitmapImageRep(cgImage: context.makeImage()!)
    try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try rep.representation(using: .png, properties: [:])!.write(to: url)
}

func background(_ c: CGContext, _ w: CGFloat, _ h: CGFloat) {
    // Flat colour keeps the PNGs tiny.
    c.setFillColor(CGColor(red: 0.11, green: 0.13, blue: 0.23, alpha: 1))
    c.fill(CGRect(x: 0, y: 0, width: w, height: h))
}

/// A rounded "screen" outline with a play triangle: generic TV symbol.
func foreground(_ c: CGContext, _ w: CGFloat, _ h: CGFloat) {
    let unit = min(w / 400, h / 240)
    let screen = CGRect(x: w / 2 - 110 * unit, y: h / 2 - 62 * unit, width: 220 * unit, height: 132 * unit)
    c.setStrokeColor(CGColor(gray: 1, alpha: 1))
    c.setLineWidth(12 * unit)
    c.addPath(CGPath(roundedRect: screen, cornerWidth: 18 * unit, cornerHeight: 18 * unit, transform: nil))
    c.strokePath()
    c.setFillColor(CGColor(red: 1, green: 0.32, blue: 0.32, alpha: 1))
    c.move(to: CGPoint(x: w / 2 - 26 * unit, y: h / 2 + 36 * unit))
    c.addLine(to: CGPoint(x: w / 2 + 40 * unit, y: h / 2 + 4 * unit))
    c.addLine(to: CGPoint(x: w / 2 - 26 * unit, y: h / 2 - 28 * unit))
    c.closePath()
    c.fillPath()
}

func imageset(_ url: URL, name: String, sizes: [(scale: Int, width: Int, height: Int)], draw: (CGContext, CGFloat, CGFloat) -> Void) throws {
    var entries: [String] = []
    for size in sizes {
        let file = "\(name)@\(size.scale)x.png"
        try png(width: size.width * size.scale, height: size.height * size.scale, to: url.appendingPathComponent(file), draw: draw)
        entries.append(#"{ "filename" : "\#(file)", "idiom" : "tv", "scale" : "\#(size.scale)x" }"#)
    }
    try write("{ \"images\" : [ \(entries.joined(separator: ", ")) ], \(info) }\n", to: url.appendingPathComponent("Contents.json"))
}

func imagestack(_ name: String, width: Int, height: Int, scales: [Int]) throws {
    let stack = brand.appendingPathComponent("\(name).imagestack")
    let sizes = scales.map { (scale: $0, width: width, height: height) }
    for (layer, draw) in [("Front", foreground), ("Back", background)] as [(String, (CGContext, CGFloat, CGFloat) -> Void)] {
        let layerURL = stack.appendingPathComponent("\(layer).imagestacklayer")
        try write("{ \(info) }\n", to: layerURL.appendingPathComponent("Contents.json"))
        try imageset(layerURL.appendingPathComponent("Content.imageset"), name: layer.lowercased(), sizes: sizes, draw: draw)
    }
    try write(#"{ "layers" : [ { "filename" : "Front.imagestacklayer" }, { "filename" : "Back.imagestacklayer" } ], \#(info) }"# + "\n",
              to: stack.appendingPathComponent("Contents.json"))
}

func both(_ c: CGContext, _ w: CGFloat, _ h: CGFloat) {
    background(c, w, h)
    foreground(c, w, h)
}

try write("{ \(info) }\n", to: catalog.appendingPathComponent("Contents.json"))
try imagestack("App Icon", width: 400, height: 240, scales: [1, 2])
try imagestack("App Icon - App Store", width: 1280, height: 768, scales: [1])
try imageset(brand.appendingPathComponent("Top Shelf Image.imageset"), name: "top-shelf",
             sizes: [(1, 1920, 720), (2, 1920, 720)], draw: both)
try imageset(brand.appendingPathComponent("Top Shelf Image Wide.imageset"), name: "top-shelf-wide",
             sizes: [(1, 2320, 720), (2, 2320, 720)], draw: both)
try write("""
{
  "assets" : [
    { "filename" : "App Icon - App Store.imagestack", "idiom" : "tv", "role" : "primary-app-icon", "size" : "1280x768" },
    { "filename" : "App Icon.imagestack", "idiom" : "tv", "role" : "primary-app-icon", "size" : "400x240" },
    { "filename" : "Top Shelf Image Wide.imageset", "idiom" : "tv", "role" : "top-shelf-image-wide", "size" : "2320x720" },
    { "filename" : "Top Shelf Image.imageset", "idiom" : "tv", "role" : "top-shelf-image", "size" : "1920x720" }
  ],
  \(info)
}

""", to: brand.appendingPathComponent("Contents.json"))
print("icons written to \(brand.path)")
