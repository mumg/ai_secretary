from io import BytesIO
from unittest import TestCase

from docx import Document
from openpyxl import Workbook
from pypdf import PdfWriter

from main import extract_docx, extract_pdf, extract_xlsx


class DocumentExtractorTests(TestCase):
    def test_docx_paragraph_and_table(self) -> None:
        document = Document()
        document.add_paragraph("Подготовить отчёт")
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Срок"
        table.cell(0, 1).text = "Пятница"
        data = BytesIO()
        document.save(data)

        output, metadata = extract_docx(data.getvalue())
        self.assertIn("Подготовить отчёт", output.render())
        self.assertIn("Срок\tПятница", output.render())
        self.assertEqual(metadata["tables"], 1)

    def test_xlsx_reads_values_without_external_links(self) -> None:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Задачи"
        sheet.append(["Задача", "Приоритет"])
        sheet.append(["Позвонить", "Высокий"])
        data = BytesIO()
        workbook.save(data)
        workbook.close()

        output, metadata = extract_xlsx(data.getvalue())
        self.assertIn("[Лист: Задачи]", output.render())
        self.assertIn("Позвонить\tВысокий", output.render())
        self.assertEqual(metadata["non_empty_cells"], 4)

    def test_pdf_reports_page_count(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        data = BytesIO()
        writer.write(data)

        output, metadata = extract_pdf(data.getvalue())
        self.assertIn("[Страница 1]", output.render())
        self.assertEqual(metadata["pages"], 1)
