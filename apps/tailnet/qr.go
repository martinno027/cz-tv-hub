package tailnet

import (
	"strings"

	qrcode "github.com/skip2/go-qrcode"
)

// QRMatrix is the login URL as a QR code for the TV screen: rows of '1' (dark) and '0' (light) separated by '\n',
// including the 4-module quiet zone. The apps draw it themselves (Android TV has no QR library).
func QRMatrix(text string) (string, error) {
	q, err := qrcode.New(text, qrcode.Medium)
	if err != nil {
		return "", err
	}
	var b strings.Builder
	for i, row := range q.Bitmap() {
		if i > 0 {
			b.WriteByte('\n')
		}
		for _, dark := range row {
			if dark {
				b.WriteByte('1')
			} else {
				b.WriteByte('0')
			}
		}
	}
	return b.String(), nil
}
