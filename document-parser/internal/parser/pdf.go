package parser

import (
	"context"
	"errors"
	"strings"

	"github.com/ledongthuc/pdf"
)

// PDF text operators encode glyph strings only. Line breaks must never pass
// through a font's ToUnicode map (code point 0x0a can be a visible glyph).
func pdfText(ctx context.Context, page pdf.Page) (string, error) {
	var output strings.Builder
	newline := func() {
		if output.Len() > 0 && !strings.HasSuffix(output.String(), "\n") {
			output.WriteByte('\n')
		}
	}
	var walk func(pdf.Value, pdf.Page, map[string]pdf.TextEncoding, int) error
	walk = func(stream pdf.Value, owner pdf.Page, inherited map[string]pdf.TextEncoding, depth int) error {
		if depth > 32 {
			return errors.New("PDF form nesting limit exceeded")
		}
		if stream.IsNull() {
			return nil
		}
		fonts := inherited
		if !owner.Resources().IsNull() {
			fonts = map[string]pdf.TextEncoding{}
			for _, name := range owner.Fonts() {
				font := owner.Font(name)
				fonts[name] = font.Encoder()
			}
		}
		var encoding pdf.TextEncoding
		var states []pdf.TextEncoding
		show := func(raw string) {
			if encoding != nil {
				raw = encoding.Decode(raw)
			}
			output.WriteString(raw)
		}
		var failure error
		var lastY float64
		positioned := false
		pdf.Interpret(stream, func(stack *pdf.Stack, operator string) {
			if err := ctx.Err(); err != nil {
				panic(err)
			}
			if output.Len() > MaxExpandedBytes {
				panic("PDF text limit exceeded")
			}
			args := make([]pdf.Value, stack.Len())
			for i := len(args) - 1; i >= 0; i-- {
				args[i] = stack.Pop()
			}
			if failure != nil {
				return
			}
			require := func(count int) {
				if len(args) != count {
					panic("invalid PDF text operator")
				}
			}
			switch operator {
			case "q":
				states = append(states, encoding)
			case "Q":
				if len(states) > 0 {
					encoding = states[len(states)-1]
					states = states[:len(states)-1]
				}
			case "BT":
				newline()
				positioned = false
			case "T*":
				newline()
			case "Tf":
				require(2)
				encoding = fonts[args[0].Name()]
			case "Td", "TD":
				require(2)
				if args[1].Float64() != 0 {
					newline()
				}
			case "Tm":
				require(6)
				y := args[5].Float64()
				if positioned && y != lastY {
					newline()
				}
				lastY = y
				positioned = true
			case "Tj":
				require(1)
				show(args[0].RawString())
			case "'":
				require(1)
				newline()
				show(args[0].RawString())
			case "\"":
				require(3)
				newline()
				show(args[2].RawString())
			case "TJ":
				require(1)
				for i := 0; i < args[0].Len(); i++ {
					item := args[0].Index(i)
					if item.Kind() == pdf.String {
						show(item.RawString())
					} else if item.Float64() <= -200 {
						output.WriteByte(' ')
					}
				}
			case "Do":
				require(1)
				form := owner.Resources().Key("XObject").Key(args[0].Name())
				if form.Key("Subtype").Name() == "Form" {
					newline()
					failure = walk(form, pdf.Page{V: form}, fonts, depth+1)
					newline()
				}
			}
		})
		return failure
	}
	err := walk(page.V.Key("Contents"), page, nil, 0)
	return output.String(), err
}
