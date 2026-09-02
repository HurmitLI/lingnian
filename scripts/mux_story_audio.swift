#!/usr/bin/env swift

import AVFoundation
import Foundation

guard CommandLine.arguments.count == 4 else {
    fputs("用法: mux_story_audio.swift <人物视频.mp4> <故事音频来源.mp4/m4a> <输出.mp4>\n", stderr)
    exit(2)
}

let videoURL = URL(fileURLWithPath: CommandLine.arguments[1])
let audioURL = URL(fileURLWithPath: CommandLine.arguments[2])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[3])

let videoAsset = AVURLAsset(url: videoURL)
let audioAsset = AVURLAsset(url: audioURL)
let composition = AVMutableComposition()

guard
    let sourceVideo = videoAsset.tracks(withMediaType: .video).first,
    let compositionVideo = composition.addMutableTrack(
        withMediaType: .video,
        preferredTrackID: kCMPersistentTrackID_Invalid
    )
else {
    fputs("错误：人物视频中没有可用的视频轨。\n", stderr)
    exit(3)
}

let videoDuration = videoAsset.duration
try compositionVideo.insertTimeRange(
    CMTimeRange(start: .zero, duration: videoDuration),
    of: sourceVideo,
    at: .zero
)
compositionVideo.preferredTransform = sourceVideo.preferredTransform

if let sourceAudio = audioAsset.tracks(withMediaType: .audio).first,
   let compositionAudio = composition.addMutableTrack(
       withMediaType: .audio,
       preferredTrackID: kCMPersistentTrackID_Invalid
   ) {
    let audioDuration = CMTimeMinimum(videoDuration, audioAsset.duration)
    try compositionAudio.insertTimeRange(
        CMTimeRange(start: .zero, duration: audioDuration),
        of: sourceAudio,
        at: .zero
    )
} else {
    fputs("错误：故事音频来源中没有可用的音频轨。\n", stderr)
    exit(4)
}

try? FileManager.default.removeItem(at: outputURL)

guard let exporter = AVAssetExportSession(
    asset: composition,
    presetName: AVAssetExportPresetHighestQuality
) else {
    fputs("错误：无法创建视频导出会话。\n", stderr)
    exit(5)
}

exporter.outputURL = outputURL
exporter.outputFileType = .mp4
exporter.shouldOptimizeForNetworkUse = true

let semaphore = DispatchSemaphore(value: 0)
exporter.exportAsynchronously {
    semaphore.signal()
}
semaphore.wait()

switch exporter.status {
case .completed:
    print(outputURL.path)
case .failed, .cancelled:
    fputs("错误：导出失败：\(exporter.error?.localizedDescription ?? "未知错误")\n", stderr)
    exit(6)
default:
    fputs("错误：导出未完成，状态 \(exporter.status.rawValue)。\n", stderr)
    exit(7)
}
