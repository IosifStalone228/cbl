# 01-eda-burglary.ipynb

import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# Load your data
df = pd.read_csv('data/2024-02/2024-02-avon-and-somerset-street.csv')

# Filter for burglary only
burglary_df = df[df['Crime type'] == 'Burglary'].copy()

# Basic stats
print(f"Total burglary reports: {len(burglary_df)}")
print("\nMissing values:\n", burglary_df.isnull().sum())

# Top 10 locations for burglary
top_locations = burglary_df['Location'].value_counts().head(10)

plt.figure(figsize=(8, 6))
sns.barplot(x=top_locations.values, y=top_locations.index, palette='Reds_r')
plt.title('Top 10 Locations for Burglary Reports')
plt.xlabel('Number of Reports')
plt.ylabel('Location')
plt.tight_layout()
plt.show()

# Outcomes for burglary
plt.figure(figsize=(8, 6))
sns.countplot(data=burglary_df, y='Last outcome category',
              order=burglary_df['Last outcome category'].value_counts().index,
              palette='Blues_r')
plt.title('Burglary - Last Outcome Category')
plt.xlabel('Count')
plt.ylabel('Outcome')
plt.tight_layout()
plt.show()

# Spatial scatterplot of burglary reports (if coordinates are present)
burglary_geo = burglary_df.dropna(subset=['Latitude', 'Longitude'])

plt.figure(figsize=(8, 6))
sns.scatterplot(data=burglary_geo, x='Longitude', y='Latitude', alpha=0.5, s=20)
plt.title('Burglary Locations - Basic Scatter Plot')
plt.xlabel('Longitude')
plt.ylabel('Latitude')
plt.tight_layout()
plt.show()

