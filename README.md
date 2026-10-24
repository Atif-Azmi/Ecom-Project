# 📚 College Library ERP Portal & E-Commerce System

> **Academic Project for: E-Commerce & Enterprise Resource Planning (ERP)**  
> Built with **Python Flask**, **SQLite / MySQL**, and modern **Soft-Neumorphic UI**.  
> Combines core ERP operations (Inventory, Supplier Procurement, Member Management, Financial Reporting) with modern E-Commerce workflows (Borrow Cart, Wishlist, Ratings & Reviews, Payment Gateway & Printable Receipts).

---

## 📌 1. Project Overview

**College Library ERP Portal** is an integrated institutional platform designed to model both enterprise operational workflows (ERP) and customer-facing digital storefront experiences (E-Commerce).

- **Enterprise Resource Planning (ERP):** Centralizes library catalog inventory, student membership records, circulation tracking, supplier purchase orders with automated stock increment, and auditable financial reports with CSV export.
- **E-Commerce Experience:** Enables students to browse textbooks, read reviews and ratings, save items to a wishlist, bundle multiple loans into a shopping-style **Borrow Cart**, receive loan order numbers (`ORD-2026-XXXX`), settle late fees through a demo payment gateway (UPI / Cards), and generate printable official receipts.

---

## 🚀 2. Quick Start Guide (Run in 30 Seconds)

The project is configured by default to run on **SQLite (`library.db`)** with zero extra setup. All tables, seed users, sample books, purchase orders, ratings, and active/overdue loans are automatically seeded on startup!

### Prerequisites
- Python 3.8+ installed (`python --version`)

### Step 1: Open Terminal in Project Folder
```bash
cd c:\Erp-Project\CloudLibrary
```

### Step 2: Install Required Dependencies
```bash
pip install -r requirements.txt
```
*(Dependencies: Flask, PyMySQL, python-dotenv, Werkzeug, gunicorn)*

### Step 3: Run the Flask Application
```bash
python app.py
```

### Step 4: Access the Web Portal
Open your web browser and navigate to:
👉 **`http://127.0.0.1:5000`**

---

## 🔑 3. Demo Login Credentials

The database automatically seeds 1 Admin account and 3 Student accounts covering diverse operational states:

| Role | Username | Password | Account Details & State |
|---|---|---|---|
| **Librarian / Admin** | `admin` | `admin123` | **Full ERP Access:** Dashboard KPIs, Inventory, Members, Purchase Orders, Reports |
| **Student 1** | `student1` | `student123` | **Aarav Sharma:** Has an **Overdue Loan** (*Cloud Computing: A Hands-on Approach*) with Rs 30.00 fine, ready to demo payment gateway |
| **Student 2** | `student2` | `student123` | **Priya Patel:** Has an **Active Loan** (*Designing Data-Intensive Applications*) |
| **Student 3** | `student3` | `student123` | **Rohan Verma:** Has a completed/returned borrowing record |

*(New student accounts can also be registered at any time via the `/register` page with automatic password hashing).*

---

## 💼 4. ERP Modules & Features

### 1. Role-Based Access Control (RBAC)
- Strict separation between **Student** and **Librarian / Admin** roles.
- Admin routes (`/admin/*`) are protected with the `@admin_required` decorator.
- Students attempting to access admin views are redirected with security warning alerts.

### 2. Admin ERP Dashboard (`/admin/dashboard`)
- **8 Real-time KPI Cards:**
  - Total Book Titles
  - Total Physical Copies
  - Copies Currently Available
  - Active Student Loans
  - Overdue Loans
  - Registered Students
  - Total Fines Collected (Rs)
  - Pending Unpaid Fines (Rs)
- **Live Inventory Utilization Dial:** Visual representation of issued vs available stock.
- **Recent Supplier Purchase Orders:** Instant overview of pending procurements with 1-click receiving.
- **Live Circulation Feed:** Real-time log of borrowing and return transactions.

### 3. Inventory Management & Stock Alerts (`/admin/books`)
- Complete CRUD operations: Add new titles, edit book metadata, adjust total copies, and delete books (with active loan safety checks).
- **Automated Low-Stock Alert Badge:** Automatically displays a bright **"Low Stock: X left"** warning badge on any title with $\le 2$ available copies.

### 4. Member Management (`/admin/members`)
- Directory of all registered student members with email, registration date, and borrowing metrics.
- Individual student profile view (`/admin/members/<user_id>`) detailing active borrowings, overdue counts, return history, and unpaid fines.

### 5. Supplier Procurement Module (`/admin/purchases`)
- Create Purchase Orders (`PO-2026-XXXX`) from registered suppliers (e.g., *Pearson Education*, *McGraw-Hill Education*, *O'Reilly Media*).
- Specify supplier name, contact, unit cost, and order quantity.
- **Automated Stock Increment on "Received":** When the admin clicks **Mark as Received**, the system automatically increments both `total_copies` and `available_copies` of the book in the catalog.

### 6. Reports & CSV Export (`/admin/reports`)
- Tabbed reports for:
  1. **Issued Books Report:** All ongoing circulation loans.
  2. **Overdue Books Report:** Loans exceeding the 14-day limit with calculated late fees (Rs 5.00/day).
  3. **Fines & Revenue Report:** Historical log of collected fines and pending unpaid penalties.
- **Instant CSV Export:** Download audit-ready CSV spreadsheets with 1 click for external accounting or faculty submission.

---

## 🛒 5. E-Commerce Modules & Features

### 1. Borrow Cart (`/student/cart`)
- Students can browse the catalog and add multiple books to their **Borrow Cart**.
- Real-time cart counter badge in the navbar navigation.
- **Checkout Flow:** Validates stock and active holds, issues books under a unified **Order Reference** (`ORD-2026-XXXX`), deducts stock, and flashes an order confirmation.

### 2. Wishlist / "Save for Later" (`/student/wishlist`)
- 1-click bookmarking of desired textbooks directly from catalog cards or product detail pages.
- Wishlist counter badge in the navigation header.
- **1-Click "Move to Cart":** Seamlessly transfers wishlisted titles into the checkout cart.

### 3. Ratings & Customer Reviews (`/book/<id>`)
- Detailed product page for every textbook.
- Visual 5-star rating summary and average rating score.
- Verified student reviews with star rating, review title, detailed comments, and post timestamps.
- Interactive review submission form for students.

### 4. Demo Payment Gateway & Receipts (`/student/pay-fine/<id>`)
- Seamless fine clearance for overdue books.
- **Interactive Payment Gateway:**
  - **UPI Option:** Preset 1-click demo options for Google Pay, PhonePe, Paytm, or custom UPI ID.
  - **Card Option:** Test debit/credit card form with pre-filled test card values.
- **Official Printable Invoice / Receipt (`/student/receipt/<id>`):**
  - Institutional header, transaction ID, payment method, student details, and fine breakdown.
  - Built-in **Print Receipt** button triggering browser print styles (`@media print`).

### 5. Automated Alerts & Notifications
- **Due Soon Notification Banner:** Persistent amber alert on student dashboard when books are due within 48 hours.
- **Overdue Warning Banner:** Highlighting urgent unpaid fines with direct payment links.
- **Real-time Flash Feedback:** Interactive feedback for cart additions, order checkouts, fine payments, and stock updates.

---

## 🧪 6. Testing & Demonstration Walkthrough

Follow these steps for a complete project evaluation or viva demonstration:

### A. Test E-Commerce Checkout Flow (Student)
1. Log in as `student2` / `student123`.
2. Visit **Books Catalog** (`/student/books`). Notice the **"Low Stock"** badge on books with $\le 2$ copies.
3. Click **Add to Cart** on 2 different books. Notice the cart count badge in the navbar increments.
4. Click **Wishlist** (heart icon) on another book.
5. Open your **Borrow Cart** (`/student/cart`). Click **"Confirm Loan Checkout"**.
6. You will receive an **Order Confirmation** with an `ORD-2026-XXXX` order number, and the books appear in **My Loans**.

### B. Test Fine Payment & Printable Receipt (Student)
1. Log in as `student1` / `student123`.
2. On the **Student Dashboard**, you will see an **Overdue Alert** for *Cloud Computing: A Hands-on Approach* with a **Rs 30.00** fine.
3. Click **"Pay Fine & Get Receipt"**.
4. Select UPI (e.g., Google Pay) or Card and click **"Complete Payment (Demo)"**.
5. You are redirected to the **Official Payment Receipt** with transaction reference. Click **"Print Receipt"** to view the clean printable layout!

### C. Test Supplier Procurement & Automated Restock (Admin)
1. Log in as `admin` / `admin123`.
2. Navigate to **Purchases** (`/admin/purchases`).
3. Notice existing purchase orders. Note the available copies of *Computer Networks*.
4. In the **"Record New Purchase Order"** form:
   - Select *Computer Networks*.
   - Supplier: `Pearson Education`.
   - Quantity: `5`.
   - Unit Cost: `650.00`.
   - Click **"Place Purchase Order"**.
5. Click **"Mark as Received"** on your new purchase order.
6. Check **Manage Books** (`/admin/books`): the stock has automatically increased by 5 copies!

### D. Test Reports & CSV Export (Admin)
1. As `admin`, navigate to **Reports** (`/admin/reports`).
2. Switch between **Issued Books**, **Overdue Books**, and **Fines & Revenue** tabs.
3. Click the **"Export CSV"** button in any tab to download the formatted spreadsheet.

---

## 📂 7. Project Architecture

```
CloudLibrary/
├── app.py                      # Flask backend (Routes, RBAC, Business Logic, DB wrapper)
├── schema.sql                  # Database schema & initial seeding data
├── requirements.txt            # Python dependencies
├── README.md                   # Complete documentation & grading reference
│
├── static/
│   ├── css/
│   │   └── neumorphic.css      # Soft-UI Neumorphic design system & print styles
│   └── js/
│       └── main.js             # Interactive client helpers & UI transitions
│
└── templates/
    ├── base.html               # Master layout with responsive navbar & status badges
    ├── index.html              # Public catalog with ratings, stock chips & quick actions
    ├── login.html              # Login portal with 1-click demo filler buttons
    ├── register.html           # Student registration page
    ├── book_detail.html        # E-Commerce product page (reviews, rating summary, cart/wishlist)
    │
    ├── student/                # Student E-Commerce Portal
    │   ├── dashboard.html      # Overview, active loans, due soon alert, fine CTA
    │   ├── books.html          # Textbook catalog with instant borrow / cart buttons
    │   ├── cart.html           # Borrowing shopping cart & checkout confirmation
    │   ├── wishlist.html       # Saved books list with 1-click move to cart
    │   ├── history.html        # Order history with Order IDs & receipt links
    │   ├── pay_fine.html       # Demo UPI & Card payment gateway
    │   └── receipt.html        # Official printable payment invoice
    │
    └── admin/                  # Enterprise Resource Planning (ERP) Portal
        ├── dashboard.html      # 8 KPI metrics, utilization gauge, recent POs
        ├── books.html          # Book catalog management with low-stock alerts
        ├── add_book.html       # New book entry form
        ├── edit_book.html      # Edit book & adjust stock levels
        ├── issued_books.html   # Circulation console with Order IDs & payment tags
        ├── members.html        # Student directory with loan & fine metrics
        ├── member_detail.html  # Detailed student loan history & profile
        ├── purchases.html      # Supplier procurement & automated restocking
        └── reports.html        # Issued, overdue, and fine audit reports with CSV export
```

---

## 🛡️ 8. Security & Engineering Standards

- **Password Security:** All passwords hashed with `werkzeug.security.generate_password_hash` (`scrypt` / `pbkdf2:sha256`).
- **CSRF Protection:** Custom session-backed CSRF tokens injected into all POST forms.
- **SQL Injection Prevention:** 100% parameterized queries using DB-API `%s` placeholders.
- **Input Validation:** Strict form validations on ISBNs, quantities, prices, and user inputs.
- **Role Guards:** `@admin_required` and `@login_required` decorators ensure proper endpoint isolation.

---

## 🎓 9. College Assignment Presentation Notes (E-Commerce & ERP)

When presenting this project to your evaluators, emphasize how the application maps to core curriculum concepts:

1. **ERP Core Principles:**
   - **Cross-Functional Integration:** How inventory stock automatically increments upon receiving a supplier purchase order without manual database edits.
   - **Financial Auditing:** Centralized tracking of receivables (unpaid fines) vs realized revenue (paid fines) with verifiable receipts.
   - **Operational Metrics:** Executive dashboard KPIs displaying real-time circulation utilization.
2. **E-Commerce Principles:**
   - **Customer Journey:** Search $\rightarrow$ Product Evaluation (Ratings & Reviews) $\rightarrow$ Shopping Cart $\rightarrow$ Multi-item Order Checkout.
   - **Payment Simulation:** Simulated digital payment gateways (UPI apps, Credit/Debit cards) reflecting modern fintech workflows.
   - **Retention Mechanisms:** Wishlist functionality allowing students to bookmark future reading lists.

---

## 🌐 10. Deploying to Vercel (1-Click Deployment)

This project includes ready-to-use **`vercel.json`** serverless configuration with automatic `/tmp` SQLite persistence.

### Quick Deployment Steps:
1. Log in to [Vercel](https://vercel.com) using your GitHub account.
2. Click **"Add New..."** $\rightarrow$ **"Project"**.
3. Import your repository: **`Atif-Azmi/Ecom-Project`**.
4. In Project Settings:
   - **Framework Preset:** Other (Vercel automatically detects `vercel.json` and Python `@vercel/python`).
   - **Root Directory:** `./`
5. Click **"Deploy"**!
6. Within 1-2 minutes, your live cloud web URL will be generated (e.g. `https://ecom-project.vercel.app`).
   - Demo accounts (`admin`, `student1`, `student2`, `student3`) and sample textbooks are seeded automatically on first visit!
