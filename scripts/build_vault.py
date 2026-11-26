"""Generates atomic Obsidian notes (YAML frontmatter + [[wikilinks]]) into knowledge_vault/.
Content is synthetic, PII-free demo policy data. status: APPROVED marks it retrievable for the demo only; it has not been reviewed by the organization."""
from pathlib import Path

NOTES = [
 ("expense-meals","finance","Meal Expense Policy","low",
  ["Meals are reimbursable up to 50 dollars per day per employee.","Alcohol is never reimbursable under the meal policy.","Meal receipts above 25 dollars must be itemized."],["expense-submission","travel-booking"]),
 ("expense-submission","finance","Expense Report Submission","medium",
  ["Expense reports must be submitted within 30 days of the expense date.","Reports above 2000 dollars require manager approval before payment.","Approved expense reports are paid in the next bi-weekly payroll cycle."],["expense-meals","approval-matrix"]),
 ("travel-booking","travel","Travel Booking Rules","low",
  ["Flights must be booked at least 14 days in advance.","Economy class is required for flights under 6 hours.","Business class is permitted for flights longer than 6 hours with director approval."],["travel-hotel","expense-submission"]),
 ("travel-hotel","travel","Hotel Policy","low",
  ["The nightly hotel cap is 220 dollars in major cities and 150 dollars elsewhere.","Hotel stays longer than 5 nights require regional lead approval.","Preferred hotels are listed in the corporate travel portal."],["travel-booking"]),
 ("leave-annual","hr","Annual Leave Policy","low",
  ["Full-time employees accrue 25 days of annual leave per year.","Up to 5 unused leave days may be carried over into the next year.","Leave requests of more than 10 consecutive days need 30 days notice."],["leave-sick","approval-matrix"]),
 ("leave-sick","hr","Sick Leave Policy","medium",
  ["Employees receive 10 paid sick days per year.","A medical certificate is required after 3 consecutive sick days.","Sick leave does not carry over to the next year."],["leave-annual"]),
 ("leave-parental","hr","Parental Leave Policy","medium",
  ["Primary caregivers receive 26 weeks of paid parental leave.","Secondary caregivers receive 8 weeks of paid parental leave.","Parental leave must be requested at least 60 days before the expected start."],["leave-annual"]),
 ("remote-work","hr","Remote Work Policy","low",
  ["Employees may work remotely up to 3 days per week.","Working from another country for more than 14 days requires tax and legal review.","Company laptops must use the corporate VPN when working remotely."],["security-devices"]),
 ("security-passwords","security","Password Policy","high",
  ["Passwords must contain at least 14 characters.","Passwords must be rotated every 180 days.","Multi-factor authentication is mandatory for all corporate accounts."],["security-devices","security-incident"]),
 ("security-devices","security","Device Security Policy","high",
  ["All laptops must have full-disk encryption enabled.","Lost or stolen devices must be reported within 4 hours.","Personal USB storage devices are prohibited on corporate laptops."],["security-incident","security-passwords"]),
 ("security-incident","security","Security Incident Response","high",
  ["Security incidents must be reported to the security operations center immediately.","Severity 1 incidents require an executive briefing within 2 hours.","Post-incident reviews are completed within 10 business days."],["security-devices"]),
 ("procurement-thresholds","procurement","Procurement Thresholds","medium",
  ["Purchases under 1000 dollars may be made with a corporate card.","Purchases between 1000 and 25000 dollars require two competitive quotes.","Purchases above 25000 dollars require a formal tender process."],["approval-matrix","vendor-onboarding"]),
 ("vendor-onboarding","procurement","Vendor Onboarding","medium",
  ["New vendors must pass a due diligence check before first payment.","Vendor bank details changes require phone verification with a known contact.","Vendor contracts above 100000 dollars require legal review."],["procurement-thresholds"]),
 ("approval-matrix","governance","Approval Matrix","high",
  ["Managers approve spending up to 5000 dollars.","Directors approve spending up to 50000 dollars.","Spending above 50000 dollars requires partner committee approval."],["procurement-thresholds","expense-submission"]),
 ("data-retention","compliance","Data Retention Policy","high",
  ["Client engagement records are retained for 7 years after engagement close.","Email correspondence is retained for 3 years.","Records under legal hold must not be deleted regardless of retention period."],["data-privacy"]),
 ("data-privacy","compliance","Data Privacy Handling","high",
  ["Personal data must be classified as confidential at collection.","Data subject access requests must be answered within 30 days.","Personal data may not be transferred outside approved regions without a transfer assessment."],["data-retention","security-incident"]),
 ("onboarding-it","it","New Joiner IT Setup","low",
  ["New joiners receive a laptop on their first day.","Account provisioning requests are completed within 2 business days.","Employees must complete security awareness training within 14 days of joining."],["security-passwords","onboarding-hr"]),
 ("onboarding-hr","hr","New Joiner HR Onboarding","low",
  ["The probation period lasts 6 months.","New joiners are assigned a buddy for the first 90 days.","Performance goals are agreed within 30 days of joining."],["onboarding-it","leave-annual"]),
 ("training-budget","hr","Training Budget","low",
  ["Each employee has an annual training budget of 2500 dollars.","External certifications require manager pre-approval.","Unused training budget does not roll over."],["approval-matrix"]),
 ("client-gifts","compliance","Client Gifts and Hospitality","high",
  ["Gifts to clients must not exceed 100 dollars in value.","All gifts above 50 dollars must be recorded in the gift register.","Gifts to government officials are prohibited."],["approval-matrix"]),
 ("conflict-interest","compliance","Conflict of Interest","high",
  ["Employees must declare outside business interests annually.","Conflicts must be disclosed to compliance within 5 business days of arising.","Employees may not work on engagements for entities in which they hold shares."],["client-gifts"]),
 ("whistleblowing","compliance","Whistleblowing Policy","high",
  ["Concerns can be reported anonymously through the ethics hotline.","Retaliation against good-faith reporters is prohibited.","Reports are acknowledged within 5 business days."],["conflict-interest"]),
 ("incident-oncall","it","On-call Rotation","medium",
  ["On-call engineers rotate weekly.","On-call engineers must acknowledge pages within 10 minutes.","On-call compensation is 150 dollars per week."],["security-incident"]),
 ("software-licenses","it","Software License Policy","medium",
  ["Software installations require approval from IT service desk.","Open source licenses must be reviewed by legal before use in client deliverables.","Unused licenses are reclaimed after 90 days of inactivity."],["procurement-thresholds"]),
 ("cloud-spend","it","Cloud Spend Governance","medium",
  ["Cloud projects must carry cost-center tags.","Budget alerts trigger at 80 percent of the monthly budget.","Idle compute resources are shut down after 14 days."],["approval-matrix"]),
 ("mileage","finance","Mileage Reimbursement","low",
  ["Mileage is reimbursed at 0.62 dollars per mile.","Commuting between home and the regular office is not reimbursable.","Mileage claims need a trip log with start and end locations."],["expense-submission"]),
 ("per-diem","travel","International Per Diem","low",
  ["International per diem is 90 dollars per day in tier 1 countries.","Per diem is reduced by 25 percent when meals are provided.","Per diem claims are submitted with the expense report."],["expense-meals","travel-booking"]),
 ("workplace-safety","hr","Workplace Safety","medium",
  ["Fire drills are held twice per year.","Workplace injuries must be reported within 24 hours.","First aid kits are located on every floor."],["whistleblowing"]),
 ("performance-review","hr","Performance Review Cycle","low",
  ["Performance reviews take place twice per year in June and December.","Ratings use a five-point scale.","Employees rated 1 enter a 90-day improvement plan."],["onboarding-hr","training-budget"]),
]

out = Path(__file__).resolve().parent.parent / "knowledge_vault"
out.mkdir(exist_ok=True)
for nid, dom, title, risk, facts, links in NOTES:
    body = "\n".join(f"- {f}" for f in facts)
    rel = ", ".join(f"[[{l}]]" for l in links)
    (out / f"{nid}.md").write_text(
        f"---\nid: {nid}\ndomain: {dom}\nrisk_tier: {risk}\nversion: 1\nstatus: APPROVED\ntitle: {title}\n---\n# {title}\n\n{body}\n\nRelated: {rel}\n")
print(len(NOTES), "notes")
