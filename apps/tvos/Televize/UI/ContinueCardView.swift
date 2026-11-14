import CTCore
import SwiftUI

/// A "Pokračovat" card of Archiv and Domů: the image with the progress over it, the title and the position (or the
/// episode). Equatable: a focus move does not redraw it.
struct ContinueCardView: View, Equatable {
    let entry: ResumeEntry

    var body: some View {
        let card = entry.card
        let main = (card?.showTitle.isEmpty ?? true) ? card?.title ?? "" : card?.showTitle ?? ""
        let second = (card?.showTitle.isEmpty ?? true) ? "" : card?.title ?? ""
        VStack(alignment: .leading, spacing: 0) {
            ZStack(alignment: .bottom) {
                ArchiveImageView(image: card?.image.flatMap(ArchiveImage.init),
                                 symbol: card?.source == .dvb ? "antenna.radiowaves.left.and.right" : "film")
                    .frame(width: ArchiveCardView.size.width, height: 225)
                    .clipped()
                ProgressView(value: entry.lengthSeconds > 0 ? min(1, entry.progressSeconds / entry.lengthSeconds) : 0)
                    .tint(.white)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 10)
            }
            VStack(alignment: .leading, spacing: 6) {
                Text(main).font(.headline).lineLimit(1)
                HStack(spacing: 14) {
                    Text(second.isEmpty ? ResumeRules.buttonTitle(entry.progressSeconds) : second)
                    Spacer(minLength: 0)
                    SourceIcon(source: card?.source == .dvb ? .dvb : .ct)
                }
                .font(.system(size: 24))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 12)
        }
        .frame(width: ArchiveCardView.size.width, height: ArchiveCardView.size.height, alignment: .topLeading)
    }
}
