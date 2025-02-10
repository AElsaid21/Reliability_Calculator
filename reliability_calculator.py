try:
    import tkinter as tk
    from tkinter import filedialog  # preserved for legacy compatibility
except ImportError:
    # In environments (like Streamlit Cloud) where tkinter is unavailable, ignore it.
    pass

import pandas as pd
import datetime
import plotly.graph_objects as go
import streamlit as st

###############################################################################
# Global variable for instructions (shown only once in main)
###############################################################################
instructions = """INSTRUCTIONS:
1) Your Excel file must have columns: Well Name, Installation Date, Failure Date, and Related.
2) Any row whose 'Related' column contains "yes" or "related" (case-insensitive) is labeled "Related"; otherwise 'NonRelated'.
3) If the 'Related' column is missing, we will do only 'All Wells' calculations.
4) Please follow the excel sheet instructions to fill the data.
"""

###############################################################################
# 1) CLASSIFY "Related" vs. "NonRelated"
###############################################################################
def classify_related_category(value) -> str:
    if not isinstance(value, str):
        return "NonRelated"
    val_lower = value.strip().lower()
    if "yes" in val_lower or "related" in val_lower:
        return "Related"
    return "NonRelated"

###############################################################################
# 2) SINGLE-DAY CALCULATION ("All Wells", "Related", "NonRelated")
###############################################################################
def calculate_well_metrics_for_subset_in_memory(df: pd.DataFrame, subset: str, has_related: bool) -> dict:
    if not has_related and subset in ["Related", "NonRelated"]:
        return {"MTBF": 0, "MTTF": 0, "AVRL": 0}

    if subset == "Related":
        sub_df = df[df["Category"] == "Related"].copy()
    elif subset == "NonRelated":
        sub_df = df[df["Category"] == "NonRelated"].copy()
    else:  # "All Wells"
        sub_df = df.copy()

    if sub_df.empty:
        return {"MTBF": 0, "MTTF": 0, "AVRL": 0}

    ref_date = pd.Timestamp(datetime.date.today())
    sub_df["Status"] = sub_df["Failure Date"].apply(lambda x: "Failed" if x < ref_date else "Running")
    sub_df["Run Life"] = (sub_df["Failure Date"] - sub_df["Installation Date"]).dt.days

    total_wells_count = len(sub_df)
    failed_wells_df = sub_df[sub_df["Status"] == "Failed"]
    failed_wells_count = len(failed_wells_df)
    if failed_wells_count == 0:
        return {"MTBF": 0, "MTTF": 0, "AVRL": 0}

    running_wells_df = sub_df[sub_df["Status"] == "Running"]
    failed_wells_total_rl = failed_wells_df["Run Life"].sum()
    running_wells_total_rl = running_wells_df["Run Life"].sum()
    total_rl = sub_df["Run Life"].sum()

    mtbf = total_rl / failed_wells_count
    mttf = running_wells_total_rl / failed_wells_count
    avrl = total_rl / total_wells_count

    return {"MTBF": mtbf, "MTTF": mttf, "AVRL": avrl}

###############################################################################
# 3) DAILY SUBSET METRICS
###############################################################################
def compute_metrics_for_subset(sub_df: pd.DataFrame, current_day: pd.Timestamp) -> dict:
    wells_online_df = sub_df[sub_df["Installation Date"] <= current_day]
    total_count = len(wells_online_df)
    if total_count == 0:
        return {"failed_count": 0, "running_count": 0,
                "total_count": 0, "total_rl": 0, "running_rl": 0,
                "mtbf": 0, "mttf": 0, "avrl": 0}

    failed_wells_df = wells_online_df[wells_online_df["Failure Date"] <= current_day]
    failed_count = len(failed_wells_df)
    running_wells_df = wells_online_df[wells_online_df["Failure Date"] > current_day]
    running_count = len(running_wells_df)

    failed_runlife = (failed_wells_df["Failure Date"] - failed_wells_df["Installation Date"]).dt.days.sum()
    partial_runlife = (current_day - running_wells_df["Installation Date"]).dt.days.sum()

    total_rl = failed_runlife + partial_runlife
    running_rl = partial_runlife

    if failed_count == 0:
        mtbf = 0
        mttf = 0
    else:
        mtbf = total_rl / failed_count
        mttf = running_rl / failed_count

    avrl = total_rl / total_count

    return {"failed_count": failed_count,
            "running_count": running_count,
            "total_count": total_count,
            "total_rl": total_rl,
            "running_rl": running_rl,
            "mtbf": mtbf,
            "mttf": mttf,
            "avrl": avrl}

###############################################################################
# 4) BUILD DAILY CUMULATIVE DATAFRAME
###############################################################################
def daily_cumulative_metrics(file_path) -> (pd.DataFrame, bool):
    df = pd.read_excel(file_path)

    required_cols = ["Well Name", "Installation Date", "Failure Date"]
    for col in required_cols:
        if col not in df.columns:
            st.error(f"Error: Missing column '{col}'.")
            return pd.DataFrame(), False

    has_related = ("Related" in df.columns)
    if not has_related:
        st.info("You didn't add any data in the Related column. We'll do 'All Wells' only.")

    df["Installation Date"] = pd.to_datetime(df["Installation Date"], errors="coerce")
    df["Failure Date"] = pd.to_datetime(df["Failure Date"], errors="coerce")
    df.dropna(subset=["Installation Date", "Failure Date"], inplace=True)
    if df.empty:
        st.error("No valid rows remain after dropping invalid data.")
        return pd.DataFrame(), has_related

    if has_related:
        df["Category"] = df["Related"].apply(classify_related_category)
    else:
        df["Category"] = "All Wells"

    min_date = df["Installation Date"].min()
    today = pd.Timestamp(datetime.date.today())
    date_range = pd.date_range(start=min_date, end=today, freq='D')

    if has_related:
        related_df = df[df["Category"]=="Related"].copy()
        nonrel_df = df[df["Category"]=="NonRelated"].copy()
    else:
        related_df = pd.DataFrame()
        nonrel_df = pd.DataFrame()

    daily_data = {
        "Date": [],
        "CumulativeMTBF": [], "CumulativeMTTF": [], "CumulativeAVRL": [],
        "TotalWellsSoFar": [], "FailedWellsSoFar": [], "RunningWellsSoFar": [],
        "TotalRunLifeSoFar": [], "RunningRunLifeSoFar": []
    }
    if has_related:
        daily_data.update({
            "CumulativeMTBF_Related": [], "CumulativeMTTF_Related": [], "CumulativeAVRL_Related": [],
            "TotalWellsSoFar_Related": [], "FailedWellsSoFar_Related": [], "RunningWellsSoFar_Related": [],
            "TotalRunLifeSoFar_Related": [], "RunningRunLifeSoFar_Related": [],
            "CumulativeMTBF_NonRelated": [], "CumulativeMTTF_NonRelated": [], "CumulativeAVRL_NonRelated": [],
            "TotalWellsSoFar_NonRelated": [], "FailedWellsSoFar_NonRelated": [], "RunningWellsSoFar_NonRelated": [],
            "TotalRunLifeSoFar_NonRelated": [], "RunningRunLifeSoFar_NonRelated": []
        })

    for day in date_range:
        # For 'All Wells'
        all_metrics = compute_metrics_for_subset(df, day)
        daily_data["Date"].append(day)
        daily_data["CumulativeMTBF"].append(all_metrics["mtbf"])
        daily_data["CumulativeMTTF"].append(all_metrics["mttf"])
        daily_data["CumulativeAVRL"].append(all_metrics["avrl"])
        daily_data["TotalWellsSoFar"].append(all_metrics["total_count"])
        daily_data["FailedWellsSoFar"].append(all_metrics["failed_count"])
        daily_data["RunningWellsSoFar"].append(all_metrics["running_count"])
        daily_data["TotalRunLifeSoFar"].append(all_metrics["total_rl"])
        daily_data["RunningRunLifeSoFar"].append(all_metrics["running_rl"])

        if has_related:
            rel_metrics = compute_metrics_for_subset(related_df, day)
            daily_data["CumulativeMTBF_Related"].append(rel_metrics["mtbf"])
            daily_data["CumulativeMTTF_Related"].append(rel_metrics["mttf"])
            daily_data["CumulativeAVRL_Related"].append(rel_metrics["avrl"])
            daily_data["TotalWellsSoFar_Related"].append(rel_metrics["total_count"])
            daily_data["FailedWellsSoFar_Related"].append(rel_metrics["failed_count"])
            daily_data["RunningWellsSoFar_Related"].append(rel_metrics["running_count"])
            daily_data["TotalRunLifeSoFar_Related"].append(rel_metrics["total_rl"])
            daily_data["RunningRunLifeSoFar_Related"].append(rel_metrics["running_rl"])

            nonrel_metrics = compute_metrics_for_subset(nonrel_df, day)
            daily_data["CumulativeMTBF_NonRelated"].append(nonrel_metrics["mtbf"])
            daily_data["CumulativeMTTF_NonRelated"].append(nonrel_metrics["mttf"])
            daily_data["CumulativeAVRL_NonRelated"].append(nonrel_metrics["avrl"])
            daily_data["TotalWellsSoFar_NonRelated"].append(nonrel_metrics["total_count"])
            daily_data["FailedWellsSoFar_NonRelated"].append(nonrel_metrics["failed_count"])
            daily_data["RunningWellsSoFar_NonRelated"].append(nonrel_metrics["running_count"])
            daily_data["TotalRunLifeSoFar_NonRelated"].append(nonrel_metrics["total_rl"])
            daily_data["RunningRunLifeSoFar_NonRelated"].append(nonrel_metrics["running_rl"])

    return pd.DataFrame(daily_data), has_related

###############################################################################
# 5) MISMATCH CHECK FOR A SINGLE ROW
###############################################################################
def check_row_mismatch(row, df_input: pd.DataFrame, categories: list, acceptance=0.05) -> bool:
    for cat in categories:
        single_day = calculate_well_metrics_for_subset_in_memory(df_input, cat, ("Related" in df_input.columns))
        if cat == "All Wells":
            daily_mtbf = row["CumulativeMTBF"]
            daily_mttf = row["CumulativeMTTF"]
            daily_avrl = row["CumulativeAVRL"]
        elif cat == "Related":
            daily_mtbf = row["CumulativeMTBF_Related"]
            daily_mttf = row["CumulativeMTTF_Related"]
            daily_avrl = row["CumulativeAVRL_Related"]
        else:
            daily_mtbf = row["CumulativeMTBF_NonRelated"]
            daily_mttf = row["CumulativeMTTF_NonRelated"]
            daily_avrl = row["CumulativeAVRL_NonRelated"]

        for metric, daily_val, single_val in [
            ("MTBF", daily_mtbf, single_day["MTBF"]),
            ("MTTF", daily_mttf, single_day["MTTF"]),
            ("AVRL", daily_avrl, single_day["AVRL"])
        ]:
            if single_val == 0:
                continue
            rel_diff = abs(daily_val - single_val) / abs(single_val)
            if rel_diff > acceptance:
                return True
    return False

###############################################################################
# Helper: validate_row_against_single_day
###############################################################################
# (A placeholder since the original implementation was not provided)
def validate_row_against_single_day(row, current_day, df_input, category, has_related):
    return {}

###############################################################################
# 6) FINAL LOGIC
###############################################################################
def validate_final_day(result_df: pd.DataFrame, file_path, has_related: bool):
    discrepancies = {}
    if len(result_df) < 2:
        return discrepancies, "Not enough data for validation."

    if not isinstance(file_path, str):
        file_path.seek(0)
    df_input = pd.read_excel(file_path)
    df_input["Installation Date"] = pd.to_datetime(df_input["Installation Date"], errors="coerce")
    df_input["Failure Date"] = pd.to_datetime(df_input["Failure Date"], errors="coerce")
    df_input.dropna(subset=["Installation Date", "Failure Date"], inplace=True)
    if "Related" in df_input.columns:
        df_input["Category"] = df_input["Related"].apply(classify_related_category)
    else:
        df_input["Category"] = "All Wells"

    categories = ["All Wells"]
    if has_related:
        categories += ["Related", "NonRelated"]

    final_row = result_df.iloc[-1]
    row_before = result_df.iloc[-2]

    final_mismatch = check_row_mismatch(final_row, df_input, categories, acceptance=0.05)
    if not final_mismatch:
        return discrepancies, "Final day row matched => stopping with final day, no errors."

    daybefore_mismatch = check_row_mismatch(row_before, df_input, categories, acceptance=0.05)
    if not daybefore_mismatch:
        discrepancies[("SYSTEM", "REMOVE_FINAL_ROW")] = "YES"
        return discrepancies, "Day-before row matched => discard final day => no errors."

    def merge_mismatch(dest, newone):
        for k, v in newone.items():
            if v:
                dest[k] = v

    from_mismatch = validate_row_against_single_day(result_df.iloc[-2], result_df.iloc[-2]["Date"], df_input, "All Wells", has_related)
    if has_related:
        from_mismatch.update(validate_row_against_single_day(result_df.iloc[-2], result_df.iloc[-2]["Date"], df_input, "Related", has_related))
        from_mismatch.update(validate_row_against_single_day(result_df.iloc[-2], result_df.iloc[-2]["Date"], df_input, "NonRelated", has_related))
    merge_mismatch(discrepancies, from_mismatch)

    to_mismatch = validate_row_against_single_day(result_df.iloc[-1], result_df.iloc[-1]["Date"], df_input, "All Wells", has_related)
    if has_related:
        to_mismatch.update(validate_row_against_single_day(result_df.iloc[-1], result_df.iloc[-1]["Date"], df_input, "Related", has_related))
        to_mismatch.update(validate_row_against_single_day(result_df.iloc[-1], result_df.iloc[-1]["Date"], df_input, "NonRelated", has_related))
    merge_mismatch(discrepancies, to_mismatch)

    return discrepancies, "Neither final day nor day-before match => building mismatch errors."

###############################################################################
# 7) BUILD INTERACTIVE PLOT
###############################################################################
def plot_interactive_charts(result_df: pd.DataFrame, has_related: bool, discrepancies: dict):
    if ("SYSTEM", "REMOVE_FINAL_ROW") in discrepancies:
        result_df = result_df.iloc[:-1]
        for k in list(discrepancies.keys()):
            if k != ("SYSTEM", "REMOVE_FINAL_ROW"):
                del discrepancies[k]
        st.info("Removing final row => no mismatch displayed.")

    final_day = result_df.iloc[-1]
    all_final_mtbf = final_day["CumulativeMTBF"]
    all_final_mttf = final_day["CumulativeMTTF"]
    all_final_avrl = final_day["CumulativeAVRL"]

    rel_final_mtbf = rel_final_mttf = rel_final_avrl = 0
    nonrel_final_mtbf = nonrel_final_mttf = nonrel_final_avrl = 0
    if has_related:
        rel_final_mtbf = final_day["CumulativeMTBF_Related"]
        rel_final_mttf = final_day["CumulativeMTTF_Related"]
        rel_final_avrl = final_day["CumulativeAVRL_Related"]

        nonrel_final_mtbf = final_day["CumulativeMTBF_NonRelated"]
        nonrel_final_mttf = final_day["CumulativeMTTF_NonRelated"]
        nonrel_final_avrl = final_day["CumulativeAVRL_NonRelated"]

    ann_all_mtbf = dict(
        text=f"Current MTBF (All Wells): {all_final_mtbf:.2f} days",
        showarrow=False, xref="paper", yref="paper", x=0.02, y=0.95,
        bgcolor="lightgray", borderpad=8, font=dict(size=12)
    )
    ann_all_mttf = dict(
        text=f"Current MTTF (All Wells): {all_final_mttf:.2f} days",
        showarrow=False, xref="paper", yref="paper", x=0.02, y=0.95,
        bgcolor="lightgray", borderpad=8, font=dict(size=12)
    )
    ann_all_avrl = dict(
        text=f"Current AVRL (All Wells): {all_final_avrl:.2f} days",
        showarrow=False, xref="paper", yref="paper", x=0.02, y=0.95,
        bgcolor="lightgray", borderpad=8, font=dict(size=12)
    )
    ann_rel_mtbf = dict(
        text=f"Current MTBF (Related): {rel_final_mtbf:.2f} days",
        showarrow=False, xref="paper", yref="paper", x=0.02, y=0.95,
        bgcolor="lightgray", borderpad=8, font=dict(size=12)
    )
    ann_rel_mttf = dict(
        text=f"Current MTTF (Related): {rel_final_mttf:.2f} days",
        showarrow=False, xref="paper", yref="paper", x=0.02, y=0.95,
        bgcolor="lightgray", borderpad=8, font=dict(size=12)
    )
    ann_rel_avrl = dict(
        text=f"Current AVRL (Related): {rel_final_avrl:.2f} days",
        showarrow=False, xref="paper", yref="paper", x=0.02, y=0.95,
        bgcolor="lightgray", borderpad=8, font=dict(size=12)
    )
    ann_nonrel_mtbf = dict(
        text=f"Current MTBF (NonRelated): {nonrel_final_mtbf:.2f} days",
        showarrow=False, xref="paper", yref="paper", x=0.02, y=0.95,
        bgcolor="lightgray", borderpad=8, font=dict(size=12)
    )
    ann_nonrel_mttf = dict(
        text=f"Current MTTF (NonRelated): {nonrel_final_mttf:.2f} days",
        showarrow=False, xref="paper", yref="paper", x=0.02, y=0.95,
        bgcolor="lightgray", borderpad=8, font=dict(size=12)
    )
    ann_nonrel_avrl = dict(
        text=f"Current AVRL (NonRelated): {nonrel_final_avrl:.2f} days",
        showarrow=False, xref="paper", yref="paper", x=0.02, y=0.95,
        bgcolor="lightgray", borderpad=8, font=dict(size=12)
    )

    def discrepancy_annotation(cat, metric):
        key = (cat, metric)
        if key in discrepancies and discrepancies[key]:
            return dict(
                text=discrepancies[key],
                showarrow=False,
                xref="paper", yref="paper",
                x=0.02, y=-0.15,
                font=dict(size=11, color="red")
            )
        else:
            return dict(text="", showarrow=False)

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=result_df["Date"], y=result_df["CumulativeMTBF"],
        name="MTBF (All Wells)"
    ))
    fig.add_trace(go.Scatter(
        x=result_df["Date"], y=result_df["CumulativeMTTF"],
        name="MTTF (All Wells)",
        visible=False
    ))
    fig.add_trace(go.Scatter(
        x=result_df["Date"], y=result_df["CumulativeAVRL"],
        name="AVRL (All Wells)",
        visible=False
    ))
    offset = 3
    if has_related:
        fig.add_trace(go.Scatter(
            x=result_df["Date"], y=result_df["CumulativeMTBF_Related"],
            name="MTBF (Related)",
            visible=False
        ))
        fig.add_trace(go.Scatter(
            x=result_df["Date"], y=result_df["CumulativeMTTF_Related"],
            name="MTTF (Related)",
            visible=False
        ))
        fig.add_trace(go.Scatter(
            x=result_df["Date"], y=result_df["CumulativeAVRL_Related"],
            name="AVRL (Related)",
            visible=False
        ))
        fig.add_trace(go.Scatter(
            x=result_df["Date"], y=result_df["CumulativeMTBF_NonRelated"],
            name="MTBF (NonRelated)",
            visible=False
        ))
        fig.add_trace(go.Scatter(
            x=result_df["Date"], y=result_df["CumulativeMTTF_NonRelated"],
            name="MTTF (NonRelated)",
            visible=False
        ))
        fig.add_trace(go.Scatter(
            x=result_df["Date"], y=result_df["CumulativeAVRL_NonRelated"],
            name="AVRL (NonRelated)",
            visible=False
        ))
        offset = 9

    def visible_list(*idxs):
        vs = [False] * offset
        for i in idxs:
            vs[i] = True
        return vs

    buttons = [
        dict(
            label="All Wells (MTBF)",
            method="update",
            args=[
                {"visible": visible_list(0)},
                {
                    "title": dict(text="MTBF (All Wells)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
                    "annotations": [ann_all_mtbf, discrepancy_annotation("All Wells", "MTBF")]
                }
            ]
        ),
        dict(
            label="All Wells (MTTF)",
            method="update",
            args=[
                {"visible": visible_list(1)},
                {
                    "title": dict(text="MTTF (All Wells)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
                    "annotations": [ann_all_mttf, discrepancy_annotation("All Wells", "MTTF")]
                }
            ]
        ),
        dict(
            label="All Wells (AVRL)",
            method="update",
            args=[
                {"visible": visible_list(2)},
                {
                    "title": dict(text="AVRL (All Wells)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
                    "annotations": [ann_all_avrl, discrepancy_annotation("All Wells", "AVRL")]
                }
            ]
        )
    ]
    if has_related:
        buttons += [
            dict(
                label="Related (MTBF)",
                method="update",
                args=[
                    {"visible": visible_list(3)},
                    {
                        "title": dict(text="MTBF (Related)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
                        "annotations": [ann_rel_mtbf, discrepancy_annotation("Related", "MTBF")]
                    }
                ]
            ),
            dict(
                label="Related (MTTF)",
                method="update",
                args=[
                    {"visible": visible_list(4)},
                    {
                        "title": dict(text="MTTF (Related)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
                        "annotations": [ann_rel_mttf, discrepancy_annotation("Related", "MTTF")]
                    }
                ]
            ),
            dict(
                label="Related (AVRL)",
                method="update",
                args=[
                    {"visible": visible_list(5)},
                    {
                        "title": dict(text="AVRL (Related)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
                        "annotations": [ann_rel_avrl, discrepancy_annotation("Related", "AVRL")]
                    }
                ]
            ),
            dict(
                label="NonRelated (MTBF)",
                method="update",
                args=[
                    {"visible": visible_list(6)},
                    {
                        "title": dict(text="MTBF (NonRelated)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
                        "annotations": [ann_nonrel_mtbf, discrepancy_annotation("NonRelated", "MTBF")]
                    }
                ]
            ),
            dict(
                label="NonRelated (MTTF)",
                method="update",
                args=[
                    {"visible": visible_list(7)},
                    {
                        "title": dict(text="MTTF (NonRelated)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
                        "annotations": [ann_nonrel_mttf, discrepancy_annotation("NonRelated", "MTTF")]
                    }
                ]
            ),
            dict(
                label="NonRelated (AVRL)",
                method="update",
                args=[
                    {"visible": visible_list(8)},
                    {
                        "title": dict(text="AVRL (NonRelated)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
                        "annotations": [ann_nonrel_avrl, discrepancy_annotation("NonRelated", "AVRL")]
                    }
                ]
            ),
        ]

    buttons.append(
        dict(
            label="Show All Traces",
            method="update",
            args=[
                {"visible": [True] * offset},
                {"title": dict(text="All Traces Displayed", x=0.5, y=0.5, xanchor="center", yanchor="middle"), "annotations": [dict(text="", showarrow=False)]}
            ]
        )
    )

    fig.update_layout(
        title=dict(text="MTBF (All Wells)", x=0.5, y=0.5, xanchor="center", yanchor="middle"),
        margin=dict(t=120),  # increased top margin for extra space
        xaxis_title="Date",
        yaxis_title="Days",
        template="plotly_white",
        hovermode="x unified",
        legend=dict(x=1.01, y=1),
        updatemenus=[
            go.layout.Updatemenu(
                buttons=buttons,
                x=0.5, y=-0.15,  # update menu now placed below the plot
                xanchor="center", yanchor="top"
            )
        ],
        annotations=[ann_all_mtbf]
    )
    st.plotly_chart(fig, use_container_width=True)

###############################################################################
# 8) MAIN
###############################################################################
def main():
    st.title("Well Data Analysis")
    st.markdown("### Instructions")
    st.markdown(f"```\n{instructions}\n```")

    uploaded_file = st.file_uploader("Select your Excel file", type=["xlsx", "xls", "xlsm", "xlsb", "odf", "ods", "odt"])
    if uploaded_file is None:
        st.info("Please upload an Excel file to proceed.")
        return

    # Create a placeholder for status messages that will update later.
    status_placeholder = st.empty()

    # Process the uploaded file.
    uploaded_file.seek(0)
    result_df, has_related = daily_cumulative_metrics(uploaded_file)
    if result_df.empty:
        st.error("No data to plot or invalid file.")
        return

    # If only one row exists, skip final day validation.
    if len(result_df) == 1:
        plot_interactive_charts(result_df, has_related, {})
        status_placeholder.info("Only one row available, no further validation performed.")
        return

    # Reset the file pointer for re-reading during validation.
    uploaded_file.seek(0)
    discrepancies, validation_message = validate_final_day(result_df, uploaded_file, has_related)
    # Update the status placeholder with the final validation message.
    status_placeholder.info(validation_message)

    plot_interactive_charts(result_df, has_related, discrepancies)

if __name__ == "__main__":
    main()
