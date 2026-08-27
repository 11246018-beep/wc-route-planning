
import pandas as pd

# Load the data
import os

# Load the data
current_dir = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(current_dir, 'processed_nodes_phase1.csv')
df = pd.read_csv(file_path)

# Check columns and data types
print("Columns:", df.columns)
print("Data types:\n", df.dtypes)

# Basic stats
print("\nDescriptive Statistics for '間數':")
print(df['間數'].describe())

# Check for missing values
print("\nMissing values:\n", df.isnull().sum())

# Spatial distribution
print("\nLat range:", df['Lat'].min(), df['Lat'].max())
print("Lon range:", df['Lon'].min(), df['Lon'].max())

# Total toilet count
total_toilets = df['間數'].sum()
print("\nTotal Toilets:", total_toilets)
