import pandas as pd
df = pd.read_csv("data/raw/streetlight_complaints.csv", low_memory=False)
print(df.shape)
info = pd.DataFrame({"dtype": df.dtypes.astype(str), "missing": df.isna().sum(), "miss%": (df.isna().mean()*100).round(1), "nunique": df.nunique()})
print(info.to_string())
print("dup rows:", df.duplicated().sum(), "dup keys:", df.unique_key.duplicated().sum())
for c in ["agency","complaint_type","descriptor","descriptor_2","location_type","status","borough","address_type","open_data_channel_type"]:
    print("\n##", c); print(df[c].value_counts(dropna=False).head(12).to_string())
for c in ["created_date","closed_date","due_date","resolution_action_updated_date"]:
    d = pd.to_datetime(df[c], errors="coerce"); print(c, d.min(), d.max(), d.isna().sum())
print(df.resolution_description.value_counts(dropna=False).head(8).to_string())
c=pd.to_datetime(df.created_date); cl=pd.to_datetime(df.closed_date)
dur=(cl-c).dt.total_seconds()/3600
print(dur.describe(percentiles=[.1,.25,.5,.75,.9,.99]))
print("negative durations:", (dur<0).sum())
print(df.groupby(c.dt.year).size())
print(df.groupby(c.dt.year).apply(lambda g: g.closed_date.notna().mean()))
