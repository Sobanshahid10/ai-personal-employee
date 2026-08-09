from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


OUTPUT = Path("output/pdf/Muhammad_Soban_Resume_Updated.pdf")
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

navy = colors.HexColor("#17233B")
blue = colors.HexColor("#2563A9")
slate = colors.HexColor("#39465A")
muted = colors.HexColor("#667085")
line = colors.HexColor("#D6DCE5")

styles = getSampleStyleSheet()
name_style = ParagraphStyle(
    "Name",
    parent=styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=20,
    leading=22,
    textColor=navy,
    alignment=TA_CENTER,
    spaceAfter=2,
)
headline_style = ParagraphStyle(
    "Headline",
    parent=styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=8.8,
    leading=11,
    textColor=blue,
    alignment=TA_CENTER,
    spaceAfter=3,
)
contact_style = ParagraphStyle(
    "Contact",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=7.5,
    leading=9.5,
    textColor=slate,
    alignment=TA_CENTER,
    spaceAfter=4,
)
section_style = ParagraphStyle(
    "Section",
    parent=styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=9.5,
    leading=11,
    textColor=navy,
    spaceBefore=4,
    spaceAfter=2,
    keepWithNext=True,
)
body_style = ParagraphStyle(
    "Body",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=7.9,
    leading=10.2,
    textColor=slate,
    spaceAfter=2,
)
role_style = ParagraphStyle(
    "Role",
    parent=body_style,
    fontName="Helvetica-Bold",
    fontSize=8.2,
    textColor=navy,
    spaceAfter=0.8,
    keepWithNext=True,
)
bullet_style = ParagraphStyle(
    "Bullet",
    parent=body_style,
    leftIndent=10,
    firstLineIndent=-6,
    bulletIndent=3,
    spaceAfter=0.8,
)
small_style = ParagraphStyle(
    "Small",
    parent=body_style,
    fontSize=7.4,
    leading=9.4,
)


def section(title: str):
    return [
        Paragraph(title.upper(), section_style),
        HRFlowable(width="100%", thickness=0.7, color=line, spaceAfter=2),
    ]


def bullet(text: str) -> Paragraph:
    return Paragraph(f"• {text}", bullet_style)


doc = SimpleDocTemplate(
    str(OUTPUT),
    pagesize=A4,
    rightMargin=14 * mm,
    leftMargin=14 * mm,
    topMargin=10 * mm,
    bottomMargin=9 * mm,
    title="Muhammad Soban Resume",
    author="Muhammad Soban",
    subject="Junior AI, Automation, Python, Computer Vision, and Frontend Engineer",
)

story = [
    Paragraph("MUHAMMAD SOBAN", name_style),
    Paragraph(
        "JUNIOR AI / AUTOMATION ENGINEER &nbsp;|&nbsp; PYTHON &nbsp;|&nbsp; COMPUTER VISION &nbsp;|&nbsp; FRONTEND",
        headline_style,
    ),
    Paragraph(
        "Lahore, Pakistan &nbsp;•&nbsp; +92 319 7004987 &nbsp;•&nbsp; "
        '<a href="mailto:sobanshahid25@gmail.com" color="#39465A">sobanshahid25@gmail.com</a><br/>'
        '<a href="https://www.linkedin.com/in/muhammad-soban-shahid" color="#2563A9">linkedin.com/in/muhammad-soban-shahid</a>'
        " &nbsp;•&nbsp; "
        '<a href="https://github.com/Sobanshahid10" color="#2563A9">github.com/Sobanshahid10</a>',
        contact_style,
    ),
]

story += section("Professional Summary")
story.append(
    Paragraph(
        "Junior AI and web developer pursuing a BS in Artificial Intelligence, with practical experience building "
        "AI automation workflows, Python services, computer-vision applications, and responsive web interfaces. "
        "Hands-on with n8n, REST APIs, FastAPI, React, OpenCV, and YOLO. Interested in junior, internship, and "
        "contract opportunities in AI/ML, automation, Python, computer vision, and frontend engineering.",
        body_style,
    )
)

story += section("Technical Skills")
story.append(
    Paragraph(
        "<b>AI &amp; Automation:</b> Machine-learning fundamentals, OpenCV, YOLO, n8n, AI workflow automation, "
        "webhooks, API integrations<br/>"
        "<b>Programming &amp; Backend:</b> Python, C++, FastAPI, Django, Java, Spring, PHP, REST APIs<br/>"
        "<b>Frontend &amp; Data:</b> React.js, JavaScript, HTML5, CSS3, responsive design, MySQL, MongoDB<br/>"
        "<b>Tools &amp; Platforms:</b> Git, GitHub, WordPress, SEO, Stripe integration",
        small_style,
    )
)

story += section("Selected Projects")
story.extend(
    [
        KeepTogether(
            [
                Paragraph(
                    '<a href="https://github.com/Sobanshahid10/AI-Resume-Screening-Bias-Detection" '
                    'color="#17233B"><b>AI Resume Screening &amp; Bias Detection</b></a> — Python, FastAPI, Celery, React',
                    role_style,
                ),
                bullet(
                    "Built an AI-assisted screening workflow with bias detection, fairness auditing, and human-in-the-loop review."
                ),
            ]
        ),
        KeepTogether(
            [
                Paragraph(
                    '<a href="https://github.com/Sobanshahid10/smart-parking-detection" '
                    'color="#17233B"><b>Smart Parking Detection</b></a> — Python, OpenCV, YOLO',
                    role_style,
                ),
                bullet(
                    "Developed a real-time computer-vision system that classifies parking spaces as occupied or vacant from video."
                ),
            ]
        ),
        KeepTogether(
            [
                Paragraph(
                    '<a href="https://github.com/Sobanshahid10/ospilot-agent" '
                    'color="#17233B"><b>OSPilot Agent</b></a> — Python, Local-first AI Automation',
                    role_style,
                ),
                bullet(
                    "Created a safety-focused agent that diagnoses workspace pressure and quarantines approved cleanup items with rollback."
                ),
            ]
        ),
    ]
)

story += section("Experience")
story.extend(
    [
        Paragraph("AI Automation Intern | Software House | 3-month internship", role_style),
        bullet("Developed n8n workflows for real business processes and integrated external APIs and AI services."),
        bullet("Worked with webhooks and data-transformation nodes to streamline repeatable operational tasks."),
        bullet("Collaborated with developers to identify automation opportunities and implement practical solutions."),
        Paragraph("Freelance Web Developer | Remote", role_style),
        bullet("Built responsive web interfaces and backend functionality based on client requirements."),
        bullet("Applied Python, JavaScript, HTML, CSS, and database skills to deliver user-focused web solutions."),
    ]
)

story += section("Education")
story.append(
    Paragraph(
        "<b>BS Artificial Intelligence</b> — University of Management &amp; Technology &nbsp;|&nbsp; Oct 2023–Present",
        body_style,
    )
)

story += section("Certifications & Additional Information")
story.append(
    Paragraph(
        "<b>Training:</b> SEO &amp; WordPress (3 months) &nbsp;•&nbsp; AI Automation / n8n Internship (3 months)<br/>"
        "<b>Languages:</b> Urdu, English &nbsp;•&nbsp; <b>Availability:</b> 15-day notice period &nbsp;•&nbsp; "
        "Open to remote and Pakistan-based roles",
        small_style,
    )
)

doc.build(story)
print(OUTPUT.resolve())
