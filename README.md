# Manufacturing Run Time Planner

A Streamlit application for planning manufacturing production and calculating machine run time requirements.

## Features

- **Product Setup**: Define products and their operations (machines and cycle times)
- **Excel Upload**: Upload Excel files to import production plan quantities
- **Manual Entry**: Manually enter production quantities for each product
- **Automatic Calculation**: Automatically calculates total machine run time requirements
- **Results Export**: Download results as CSV

## Installation

1. Install the required dependencies:
```bash
pip install -r requirements.txt
```

## Running the App

Run the Streamlit app:
```bash
streamlit run app.py
```

The app will open in your default web browser at `http://localhost:8501`

## Excel File Format

When uploading an Excel file, ensure it contains:
- A column with **Product IDs** (e.g., `POC-S03C`, `POC-S03B`, etc.)
- A column with **Quantities/Units** (numeric values)

The app will automatically detect and let you select which columns contain the Product ID and Quantity data.

### Example Excel Format:

| Product ID | Quantity |
|------------|----------|
| POC-S03C    | 100      |
| POC-S03B    | 50       |
| POC-S04A    | 75       |

## Usage

1. **Product Setup Tab**:
   - View all existing products and their operations
   - Add new products with their machine operations
   - Delete products if needed

2. **Monthly Planning & Results Tab**:
   - **Upload Excel File**: Upload your production plan Excel file
   - **Manual Entry**: Manually enter quantities for each product
   - **View Results**: See calculated machine run times in seconds, minutes, and hours
   - **Download Results**: Export the results as CSV

## Product Data

The app comes pre-loaded with sample product data from the original HTML application. All products include:
- Product ID (e.g., `POC-S03C`)
- Product Name
- Operations with Machine IDs and Cycle Times (in seconds)

## Notes

- Cycle times are stored in seconds
- Machine run times are calculated by multiplying quantity × cycle time for each operation
- Results show total time per machine across all products
- The app supports case-insensitive product ID matching when importing from Excel

