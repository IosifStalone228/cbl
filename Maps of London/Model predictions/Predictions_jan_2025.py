import os
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt

# === CONFIGURE THESE PATHS ===
shapefile_path = r"C:\Users\20234289\TU Eindhoven\Heimans, Pepijn - CBL - Group 35\LSOA and Ward files\Lsoa from Nikita\LSOA_2021_EW_BSC_V4.shp"
csv_path       = r"C:\Users\20234289\Documents\GitHub\cbl\Maps of London\Model predictions\jan2025_burglary_predictions.csv"
# =============================

# Load the LSOA shapefile
lsoa_gdf = gpd.read_file(shapefile_path)

# Load the predictions CSV
pred_df = pd.read_csv(csv_path, dtype={"LSOA_code": str})

# Define all London borough names for filtering
boroughs = [
    "Barking and Dagenham","Barnet","Bexley","Brent","Bromley","Camden","Croydon","Ealing",
    "Enfield","Greenwich","Hackney","Hammersmith and Fulham","Haringey","Harrow","Havering",
    "Hillingdon","Hounslow","Islington","Kensington and Chelsea","Kingston upon Thames","Lambeth",
    "Lewisham","Merton","Newham","Redbridge","Richmond upon Thames","Southwark","Sutton",
    "Tower Hamlets","Waltham Forest","Wandsworth","Westminster"
]
pattern = "|".join(boroughs)

# Filter to LSOAs in London by matching borough names in LSOA21NM
london_lsoa = lsoa_gdf[lsoa_gdf['LSOA21NM'].str.contains(pattern, regex=True, na=False)]

# Merge spatial data with predictions
merged = london_lsoa.merge(
    pred_df,
    left_on='LSOA21CD',
    right_on='LSOA_code',
    how='inner'
)

# Create output folder
output_folder = "model_burglaries"
os.makedirs(output_folder, exist_ok=True)

# Function to plot and save a choropleth with a fixed 0–35 scale
def plot_choropleth_fixed_range(gdf, column, cmap, title, out_fname, vmin=0, vmax=35):
    fig, ax = plt.subplots(1, 1, figsize=(10, 12))
    gdf.plot(
        column=column,
        cmap=cmap,
        legend=True,
        vmin=vmin,
        vmax=vmax,
        legend_kwds={
            "label": f"{title}\n(range 0–35)",
            "shrink": 0.6
        },
        ax=ax
    )
    ax.set_title(title, fontsize=15)
    ax.axis('off')
    plt.tight_layout()
    plt.savefig(os.path.join(output_folder, out_fname), dpi=300)
    plt.close(fig)
    print(f"Saved {out_fname}")

# Plot predicted burglaries with 0–35 scale
plot_choropleth_fixed_range(
    merged,
    column='predicted_burglaries',
    cmap='OrRd',
    title='Predicted Burglaries per LSOA (January 2025)',
    out_fname='predicted_burglaries_map_fixed35.png'
)

# Plot actual burglaries with 0–35 scale
plot_choropleth_fixed_range(
    merged,
    column='actual_burglaries',
    cmap='OrRd',
    title='Actual Burglaries per LSOA (January 2025)',
    out_fname='actual_burglaries_map_fixed35.png'
)
