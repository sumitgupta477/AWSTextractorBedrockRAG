"""Generate the fictional test document used by the RAG eval.

The facts are invented and distinctive (odd amounts, made-up names) so a
correct answer can only come from retrieval, not from the model's general
knowledge. Requires: pip install reportlab
"""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

PAGES = [
    ("Northwind Robotics Travel and Expense Policy (Version 4.2, effective 1 March 2026)", [
        ("1. Scope", "This policy applies to all Northwind Robotics employees and contractors travelling on "
         "company business. The policy owner is the Finance Operations team, led by Priya Raman."),
        ("2. Booking flights", "All flights must be booked through the internal portal called TripDesk at least "
         "14 days before departure. Economy class is required for flights under 6 hours. Premium economy is "
         "allowed for flights between 6 and 9 hours. Business class is only allowed for flights longer than "
         "9 hours and requires written approval from a Vice President."),
        ("3. Hotels", "The nightly hotel limit is 245 Canadian dollars in Calgary, Toronto and Vancouver, "
         "and 190 Canadian dollars in all other Canadian cities. For travel to the United States the limit is "
         "275 US dollars per night. Employees may not book short-term rentals such as Airbnb without approval "
         "from their manager."),
    ]),
    ("Meals, Ground Transport and Claims", [
        ("4. Meals", "The daily meal allowance (per diem) is 82 Canadian dollars. Alcohol is never reimbursed, "
         "except at client dinners approved in advance by a director, where it is capped at 40 Canadian "
         "dollars per person."),
        ("5. Ground transport and mileage", "Use of a personal vehicle is reimbursed at 0.71 Canadian dollars "
         "per kilometre. Rental cars must be compact or mid-size and booked through TripDesk. Taxis and "
         "ride-share are allowed when public transit would take more than 45 minutes longer."),
        ("6. Submitting expenses", "Expense claims must be submitted in the Ledgerly app within 30 days of "
         "returning from a trip. Receipts are required for every expense over 25 Canadian dollars. Claims "
         "submitted after 60 days are rejected unless the Chief Financial Officer grants an exception."),
        ("7. Lost receipts", "If a receipt is lost, the employee must complete the Missing Receipt Declaration "
         "form. This option can be used at most three times per calendar year."),
    ]),
]


def build(path="northwind_travel_policy.pdf"):
    styles = getSampleStyleSheet()
    story = []
    for i, (title, sections) in enumerate(PAGES):
        if i:
            story.append(PageBreak())
        story.append(Paragraph(title, styles["Title"]))
        for heading, body in sections:
            story += [Spacer(1, 10), Paragraph(heading, styles["Heading2"]), Paragraph(body, styles["BodyText"])]
    SimpleDocTemplate(path, pagesize=letter, title="Northwind Robotics Travel Policy").build(story)


if __name__ == "__main__":
    build()
