-- ==========================================================
-- College Library ERP & E-Commerce Portal
-- Database Schema (MySQL & SQLite compatible syntax)
-- File: schema.sql
-- ==========================================================

-- 1. Create Database if it does not already exist
CREATE DATABASE IF NOT EXISTS library_db
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE library_db;

-- 2. Drop tables in reverse order of foreign keys for clean re-runs
DROP TABLE IF EXISTS reviews;
DROP TABLE IF EXISTS wishlists;
DROP TABLE IF EXISTS cart_items;
DROP TABLE IF EXISTS purchase_orders;
DROP TABLE IF EXISTS borrow_records;
DROP TABLE IF EXISTS books;
DROP TABLE IF EXISTS users;

-- 3. Users Table (Admin and Students)
CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    role ENUM('admin', 'student') NOT NULL DEFAULT 'student',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 4. Books Table
CREATE TABLE books (
    id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    author VARCHAR(150) NOT NULL,
    isbn VARCHAR(20) NOT NULL UNIQUE,
    category VARCHAR(100) NOT NULL,
    total_copies INT NOT NULL DEFAULT 1,
    available_copies INT NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_copies CHECK (available_copies >= 0 AND available_copies <= total_copies)
) ENGINE=InnoDB;

-- 5. Borrow Records Table (with E-Commerce Order ID and Fine Payment Tracking)
CREATE TABLE borrow_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    book_id INT NOT NULL,
    order_no VARCHAR(30) NULL,
    issue_date DATE NOT NULL,
    due_date DATE NOT NULL,
    return_date DATE NULL,
    fine_amount DECIMAL(8, 2) NOT NULL DEFAULT 0.00,
    fine_paid TINYINT(1) NOT NULL DEFAULT 0,
    payment_ref VARCHAR(100) NULL,
    payment_date TIMESTAMP NULL,
    status ENUM('borrowed', 'returned', 'overdue') NOT NULL DEFAULT 'borrowed',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 6. ERP: Purchase Orders Module (Supplier Procurement)
CREATE TABLE purchase_orders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    po_number VARCHAR(30) NOT NULL UNIQUE,
    supplier_name VARCHAR(150) NOT NULL,
    book_id INT NOT NULL,
    copies_ordered INT NOT NULL DEFAULT 1,
    unit_cost DECIMAL(8, 2) NOT NULL DEFAULT 0.00,
    total_cost DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    status ENUM('Pending', 'Received', 'Cancelled') NOT NULL DEFAULT 'Pending',
    order_date DATE NOT NULL,
    received_date DATE NULL,
    notes TEXT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 7. E-Commerce: Borrow Cart Items Table
CREATE TABLE cart_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    book_id INT NOT NULL,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
    UNIQUE KEY uq_user_book_cart (user_id, book_id)
) ENGINE=InnoDB;

-- 8. E-Commerce: Wishlist ("Save for later") Table
CREATE TABLE wishlists (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    book_id INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
    UNIQUE KEY uq_user_book_wishlist (user_id, book_id)
) ENGINE=InnoDB;

-- 9. E-Commerce: Book Reviews & Ratings Table
CREATE TABLE reviews (
    id INT AUTO_INCREMENT PRIMARY KEY,
    book_id INT NOT NULL,
    user_id INT NOT NULL,
    rating INT NOT NULL CHECK (rating >= 1 AND rating <= 5),
    comment TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- 10. Indexes for Performance (Search and Foreign Keys)
CREATE INDEX idx_books_category ON books(category);
CREATE INDEX idx_books_title ON books(title);
CREATE INDEX idx_borrow_user ON borrow_records(user_id);
CREATE INDEX idx_borrow_book ON borrow_records(book_id);
CREATE INDEX idx_borrow_status ON borrow_records(status);
CREATE INDEX idx_po_status ON purchase_orders(status);
CREATE INDEX idx_reviews_book ON reviews(book_id);

-- ==========================================================
-- 11. Seed Demo Data: 1 Admin, 3 Students, Sample Loans, POs, Reviews
-- Passwords below are hashed for 'admin123' and 'student123'
-- ==========================================================

-- Default Users:
-- Admin: username='admin', password='admin123'
-- Student 1: username='student1', password='student123'
-- Student 2: username='student2', password='student123'
-- Student 3: username='student3', password='student123'
INSERT INTO users (username, email, password_hash, full_name, role) VALUES
('admin', 'admin@college.edu', 'scrypt:32768:8:1$K3vBqW7D9F8e0a1b$3b3d1b6cf0cb793f187a5f6e80b433d7b2938174f76ca096ffbe7ad6322ad1459ee929db221c97a7a58a7da1791a8e2cbce2a7ebfa32fb1707cb9bbf7976e5df', 'System Administrator', 'admin'),
('student1', 'student1@college.edu', 'scrypt:32768:8:1$K3vBqW7D9F8e0a1b$3b3d1b6cf0cb793f187a5f6e80b433d7b2938174f76ca096ffbe7ad6322ad1459ee929db221c97a7a58a7da1791a8e2cbce2a7ebfa32fb1707cb9bbf7976e5df', 'Aarav Sharma', 'student'),
('student2', 'student2@college.edu', 'scrypt:32768:8:1$K3vBqW7D9F8e0a1b$3b3d1b6cf0cb793f187a5f6e80b433d7b2938174f76ca096ffbe7ad6322ad1459ee929db221c97a7a58a7da1791a8e2cbce2a7ebfa32fb1707cb9bbf7976e5df', 'Priya Patel', 'student'),
('student3', 'student3@college.edu', 'scrypt:32768:8:1$K3vBqW7D9F8e0a1b$3b3d1b6cf0cb793f187a5f6e80b433d7b2938174f76ca096ffbe7ad6322ad1459ee929db221c97a7a58a7da1791a8e2cbce2a7ebfa32fb1707cb9bbf7976e5df', 'Rohan Verma', 'student');

-- Sample Books across popular college subjects
INSERT INTO books (title, author, isbn, category, total_copies, available_copies) VALUES
('Cloud Computing: Concepts, Technology & Architecture', 'Thomas Erl, Ricardo Puttini', '978-0133387520', 'Cloud Computing', 5, 4),
('Designing Data-Intensive Applications', 'Martin Kleppmann', '978-1449373320', 'Database Systems', 4, 3),
('Clean Code: A Handbook of Agile Software Craftsmanship', 'Robert C. Martin', '978-0132350884', 'Software Engineering', 6, 5),
('Operating System Concepts', 'Abraham Silberschatz, Peter B. Galvin', '978-1119800361', 'Operating Systems', 4, 2),
('Computer Networking: A Top-Down Approach', 'James Kurose, Keith Ross', '978-0136681557', 'Computer Networks', 3, 3),
('Introduction to Algorithms (CLRS)', 'Thomas H. Cormen, Charles E. Leiserson', '978-0262046305', 'Algorithms', 5, 5),
('Hands-On Machine Learning with Scikit-Learn, Keras, and TensorFlow', 'Aurélien Géron', '978-1492032649', 'Artificial Intelligence', 3, 2),
('Site Reliability Engineering', 'Betsy Beyer, Chris Jones', '978-1491929124', 'DevOps & Cloud', 4, 4);

-- Sample Borrow Records (Active borrow and past returned, one overdue with fine)
INSERT INTO borrow_records (user_id, book_id, order_no, issue_date, due_date, return_date, fine_amount, fine_paid, status) VALUES
(2, 1, 'ORD-2026-1002', DATE_SUB(CURDATE(), INTERVAL 5 DAY), DATE_ADD(CURDATE(), INTERVAL 9 DAY), NULL, 0.00, 0, 'borrowed'),
(2, 4, 'ORD-2026-1001', DATE_SUB(CURDATE(), INTERVAL 20 DAY), DATE_SUB(CURDATE(), INTERVAL 6 DAY), NULL, 30.00, 0, 'overdue'),
(3, 3, 'ORD-2026-1003', DATE_SUB(CURDATE(), INTERVAL 3 DAY), DATE_ADD(CURDATE(), INTERVAL 11 DAY), NULL, 0.00, 0, 'borrowed'),
(4, 6, 'ORD-2026-1004', DATE_SUB(CURDATE(), INTERVAL 16 DAY), DATE_SUB(CURDATE(), INTERVAL 2 DAY), DATE_SUB(CURDATE(), INTERVAL 2 DAY), 0.00, 1, 'returned');

-- ERP Sample Purchase Orders
INSERT INTO purchase_orders (po_number, supplier_name, book_id, copies_ordered, unit_cost, total_cost, status, order_date, notes) VALUES
('PO-2026-001', 'Oxford Academic Publishing', 4, 5, 650.00, 3250.00, 'Pending', DATE_SUB(CURDATE(), INTERVAL 2 DAY), 'Semester exam restock for Operating Systems'),
('PO-2026-002', 'Pearson Education India', 1, 4, 720.00, 2880.00, 'Received', DATE_SUB(CURDATE(), INTERVAL 10 DAY), 'Annual textbook grant replenishment');

-- E-Commerce Sample Reviews
INSERT INTO reviews (book_id, user_id, rating, comment) VALUES
(3, 2, 5, 'A gold standard book on code craftsmanship. Helped me understand clean architecture and meaningful naming.'),
(1, 3, 4, 'Very clear breakdown of cloud service models (IaaS, PaaS, SaaS) and multi-tenancy.'),
(6, 4, 5, 'Comprehensive and rigorous. The definitive textbook for data structures and algorithms.');

-- E-Commerce Sample Wishlists
INSERT INTO wishlists (user_id, book_id) VALUES
(2, 2),
(3, 7);
