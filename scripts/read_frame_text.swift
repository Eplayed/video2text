import Foundation
import Vision

// 输出顺序与输入一一对应。读取失败的画面保留空槽。
let paths = CommandLine.arguments.dropFirst()
let result: [[String]] = paths.map { path in
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    let url = URL(fileURLWithPath: path)
    do {
        try VNImageRequestHandler(url: url, options: [:]).perform([request])
        return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    } catch {
        return []
    }
}
let data = try JSONEncoder().encode(result)
FileHandle.standardOutput.write(data)
