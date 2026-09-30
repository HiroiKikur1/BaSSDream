using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using BassStation.Models;
using BassStation.Services;

namespace BassStation.Views;

public partial class PracticeCalendarWindow : Window
{
    public event Action? RequestClose;
    private readonly DatabaseService _dbService = new();
    private DateTime _displayMonth = new(DateTime.Today.Year, DateTime.Today.Month, 1);
    private PracticeStatsModel? _stats;

    public PracticeCalendarWindow()
    {
        InitializeComponent();
        if (Content is FrameworkElement __root) __root.Loaded += PracticeCalendarWindow_Loaded;
    }

    private async void PracticeCalendarWindow_Loaded(object sender, RoutedEventArgs e)
    {
        await RefreshDataAsync();
    }

    private async Task RefreshDataAsync()
    {
        _stats = await _dbService.LoadPracticeStatsAsync();

        txtTotalHours.Text = _stats.TotalHours.ToString("F1");
        txtStreakDays.Text = _stats.StreakDays.ToString();
        txtTotalSessions.Text = _stats.TotalSessions.ToString();
        txtTodayMinutes.Text = Math.Round(_stats.TodayMinutes).ToString();

        lstRecentSessions.ItemsSource = _stats.RecentSessions;

        RenderMonthCalendar();
    }

    private void RenderMonthCalendar()
    {
        txtCurrentMonth.Text = $"{_displayMonth.Year} 年 {_displayMonth.Month} 月";
        gridCalendarDays.Children.Clear();

        var firstDayOfMonth = new DateTime(_displayMonth.Year, _displayMonth.Month, 1);
        int daysInMonth = DateTime.DaysInMonth(_displayMonth.Year, _displayMonth.Month);

        // Day of week offset: Monday = 0, Sunday = 6
        int startDayOffset = ((int)firstDayOfMonth.DayOfWeek + 6) % 7;

        var prevMonth = firstDayOfMonth.AddMonths(-1);
        int prevMonthDays = DateTime.DaysInMonth(prevMonth.Year, prevMonth.Month);

        // 1. Previous month trailing days
        for (int i = startDayOffset - 1; i >= 0; i--)
        {
            int d = prevMonthDays - i;
            var dt = new DateTime(prevMonth.Year, prevMonth.Month, d);
            gridCalendarDays.Children.Add(CreateDayCard(dt, isCurrentMonth: false));
        }

        // 2. Current month days
        for (int day = 1; day <= daysInMonth; day++)
        {
            var dt = new DateTime(_displayMonth.Year, _displayMonth.Month, day);
            gridCalendarDays.Children.Add(CreateDayCard(dt, isCurrentMonth: true));
        }

        // 3. Next month leading days to complete 42 cells (6 rows * 7 cols)
        int totalCells = startDayOffset + daysInMonth;
        int nextMonthCells = (42 - totalCells);
        var nextMonth = firstDayOfMonth.AddMonths(1);
        for (int day = 1; day <= nextMonthCells; day++)
        {
            var dt = new DateTime(nextMonth.Year, nextMonth.Month, day);
            gridCalendarDays.Children.Add(CreateDayCard(dt, isCurrentMonth: false));
        }
    }

    private Border CreateDayCard(DateTime dt, bool isCurrentMonth)
    {
        string dateKey = dt.ToString("yyyy-MM-dd");
        double minutes = 0;
        if (_stats != null && _stats.DailyMinutes.TryGetValue(dateKey, out double m))
        {
            minutes = m;
        }

        var dayModel = new CalendarDayModel
        {
            Date = dt,
            IsCurrentMonth = isCurrentMonth,
            PracticeMinutes = minutes
        };

        var border = new Border
        {
            Margin = new Thickness(2),
            CornerRadius = new CornerRadius(8),
            BorderThickness = new Thickness(1),
            Background = (SolidColorBrush)new BrushConverter().ConvertFromString(dayModel.BackgroundColor)!,
            BorderBrush = (SolidColorBrush)new BrushConverter().ConvertFromString(dayModel.BorderColor)!,
            Height = 44
        };

        var grid = new Grid { Margin = new Thickness(4) };

        // Day number
        var txtDay = new TextBlock
        {
            Text = dayModel.DayNumber.ToString(),
            FontSize = 11,
            FontWeight = FontWeights.Bold,
            Foreground = (SolidColorBrush)new BrushConverter().ConvertFromString(dayModel.TextColor)!,
            HorizontalAlignment = HorizontalAlignment.Left,
            VerticalAlignment = VerticalAlignment.Top
        };
        grid.Children.Add(txtDay);

        // Practice minutes badge
        if (dayModel.HasPracticed)
        {
            var txtMins = new TextBlock
            {
                Text = dayModel.DisplayMinutes,
                FontSize = 10,
                FontWeight = FontWeights.Black,
                FontFamily = (FontFamily)Application.Current.FindResource("NumFont"),
                Foreground = Brushes.White,
                HorizontalAlignment = HorizontalAlignment.Right,
                VerticalAlignment = VerticalAlignment.Bottom
            };
            grid.Children.Add(txtMins);
        }

        border.Child = grid;
        return border;
    }

    private void BtnPrevMonth_Click(object sender, RoutedEventArgs e)
    {
        _displayMonth = _displayMonth.AddMonths(-1);
        RenderMonthCalendar();
    }

    private void BtnNextMonth_Click(object sender, RoutedEventArgs e)
    {
        _displayMonth = _displayMonth.AddMonths(1);
        RenderMonthCalendar();
    }

}

