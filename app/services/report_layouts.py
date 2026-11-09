"""Approved low daily calendar and visual weekly report, rendered without a browser."""
from datetime import timedelta
from io import BytesIO
from functools import lru_cache
import unicodedata
from PIL import Image, ImageDraw, ImageFont
from app.services.weekly_report_card import ASSETS, INK, BROWN, MUTED, BEIGE, PALE, period_text, plural
from app.services.stats_common import estimate_waste_metrics, format_mass
from app.services.report_animals import mass_comparison

WATER = '#3DA5FF'
TEXT_GAP = 8
# Stay within Telegram's photo dimensions even for unusually large groups.
MAX_REPORT_PARTICIPANTS = 200
PARTICIPANT_ROW_HEIGHT = 68


@lru_cache(maxsize=90)
def animal_icon(icon, width, height):
    image = Image.open(ASSETS / f'animal-{icon}.png').convert('RGBA')
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    return image


def participants_height(card):
    if card.private or not card.participants:
        return 0
    shown = min(len(card.participants), MAX_REPORT_PARTICIPANTS)
    return 44 + ((shown + 1) // 2) * PARTICIPANT_ROW_HEIGHT + (26 if shown < len(card.participants) else 0)


def draw_participants(c, y, card):
    """Two compact rows of text per person; no individual large animal cards."""
    if not participants_height(card):
        return
    c.text(48, y, 'Участники · примерная масса', 25, weight=650)
    shown = card.participants[:MAX_REPORT_PARTICIPANTS]
    for n, participant in enumerate(shown):
        x, top = 48 + (n % 2) * 382, y + 44 + (n // 2) * PARTICIPANT_ROW_HEIGHT
        c.text(x, top, participant.name, 22, width=230)
        c.text(x + 362, top, f'{participant.count} отм.', 19, BROWN, 600, width=120, anchor='rt')
        mass, _, _ = estimate_waste_metrics(participant.count)
        comparison = mass_comparison(mass / 1000)
        if comparison:
            animal, label = comparison
            silhouette = animal_icon(animal.icon, 44, 30)
            c.im.paste(silhouette, (x + (44-silhouette.width)//2, top+27+(30-silhouette.height)//2), silhouette)
            value = f'{format_mass(mass).replace(".", ",")} · {label}'
            size = 18
            while size > 14 and c.d.textlength(value, font=c.font(size)) > 306:
                size -= 1
            c.text(x+56, top+32, value, size, MUTED, width=306)
        c.d.line((x, top+62, x+362, top+62), fill='#eee8e3')
    if len(card.participants) > len(shown):
        c.text(48, y+44+((len(shown)+1)//2)*PARTICIPANT_ROW_HEIGHT,
               f'Показаны {len(shown)} из {len(card.participants)} · полный список: /stats', 18, MUTED, width=744)


class Canvas:
    def __init__(self,height):
        self.im=Image.new('RGB',(840,height),'white'); self.d=ImageDraw.Draw(self.im); self.fonts={}
        self.poop=Image.open(ASSETS/'poop-day.png').convert('RGBA').resize((78,75),Image.Resampling.LANCZOS)
    def font(self,size,weight=400):
        key=(size,weight)
        if key not in self.fonts:self.fonts[key]=ImageFont.truetype(str(ASSETS/f'Inter-{weight}.ttf'),size)
        return self.fonts[key]
    def text(self,x,y,value,size=23,color=INK,weight=400,width=None,anchor='lt'):
        value=' '.join(''.join(c for c in str(value) if unicodedata.category(c) not in ('Cc','Cf','So')).split())
        f=self.font(size,weight)
        if width:
            original=value
            while value and self.d.textlength(value,font=f)>width:
                original=original[:-1];value=original+'…'
        self.d.text((x,y),value,font=f,fill=color,anchor=anchor)
    def logo(self,x=48,y=36,width=260,height=65):
        logo=Image.open(ASSETS/'logo.png').convert('RGBA');logo.thumbnail((width,height),Image.Resampling.LANCZOS)
        self.im.paste(logo,(x,y),logo)
    def calendar(self,card,weekday_y,top):
        start=card.start
        assert len(card.days)==7 and start.weekday()==0
        for n,(weekday,count) in enumerate(zip(('ПН','ВТ','СР','ЧТ','ПТ','СБ','ВС'),card.days)):
            cx=48+(744/7)*(n+.5)
            self.text(cx,weekday_y,weekday,23,BEIGE,650,anchor='mt')
            value=(start+timedelta(days=n)).day
            if count:
                self.im.paste(self.poop,(round(cx-39),top+2),self.poop)
                self.text(cx,top+29,value,30,'white',500,anchor='mt')
                self.d.ellipse((cx+10,top,cx+41,top+31),fill='#ffdb00')
                label=str(count) if count<1000 else '999+'
                self.text(cx+25.5,top+(6 if len(label)<3 else 9),label,
                    21 if len(label)==1 else 17 if len(label)==2 else 12,BROWN,650,anchor='mt')
            else:
                self.d.ellipse((cx-34,top+10,cx+34,top+78),fill=PALE)
                self.text(cx,top+29,value,30,BEIGE,500,anchor='mt')
    def png(self):
        output = BytesIO()
        self.im.save(output, format='PNG', optimize=True)
        return output.getvalue()

def render_daily(card):
    c = Canvas(328)
    c.logo(48, 28, 210, 53)
    date_label = period_text(card.start, card.end)
    size = 25
    while c.d.textlength(date_label, font=c.font(size, 500)) > 490:
        size -= 1
    c.text(792, 43, date_label, size, BROWN, 500, anchor='rt')
    c.calendar(card, 116, 158)
    total, members = card.total, len(card.participants)
    summary = f'{total} {plural(total, "отметка", "отметки", "отметок")} за неделю'
    if not card.private:
        summary += f' · {members} {plural(members, "участник", "участника", "участников")}'
    c.text(420, 275, summary, 25, MUTED, width=744, anchor='mt')
    return c.png()


def metric_row(c, columns, *, top, height, width, value_size, label_size):
    """Use shared baselines and center the two ink bands inside their tile."""
    values = [str(value) for _, value, _, _ in columns]
    labels = [label for _, _, label, _ in columns]

    def ink_band(font, strings):
        boxes = [font.getbbox(string, anchor='ls') for string in strings]
        return min(box[1] for box in boxes), max(box[3] for box in boxes)

    while label_size > 1 and any(c.font(label_size).getlength(s) > width for s in labels):
        label_size -= 1
    label_top, label_bottom = ink_band(c.font(label_size), labels)
    while True:
        font = c.font(value_size, 650)
        value_top, value_bottom = ink_band(font, values)
        stack_height = value_bottom - value_top + TEXT_GAP + label_bottom - label_top
        if value_size <= 1 or (stack_height <= height and all(font.getlength(s) <= width for s in values)):
            break
        value_size -= 1
    baseline = top + (height - stack_height)//2 - value_top
    label_baseline = baseline + value_bottom + TEXT_GAP - label_top
    for x, value, label, color in columns:
        c.text(x, baseline, value, value_size, color, 650, anchor='ls')
        c.text(x, label_baseline, label, label_size, anchor='ls')
    return baseline


def distribution_height(counts):
    return 78 if not sum(counts[:-1]) else 78 + 38 * sum(n > 0 for n in counts)


def distribution(c, y, title, counts, labels, x=442):
    height = distribution_height(counts)
    c.d.rounded_rectangle((x, y, x+350, y + height), radius=20, fill=PALE)
    total, known = sum(counts), sum(counts[:-1])
    if not known:
        value = 'Пока нет отметок' if not total else f'Нет ответов · {total} {plural(total, "отметка", "отметки", "отметок")}'
        title_height = c.font(23, 650).getbbox(title, anchor='lt')[3]
        value_height = c.font(19).getbbox(value, anchor='lt')[3]
        title_y = y + (height - title_height - TEXT_GAP - value_height)//2
        c.text(x+22, title_y, title, 23, weight=650)
        c.text(x+22, title_y + title_height + TEXT_GAP, value, 19, MUTED, width=306)
        return
    title_y = y + 15
    c.text(x+22, title_y, title, 23, weight=650)
    c.text(x+22, title_y + c.font(23, 650).getbbox(title, anchor='lt')[3] + TEXT_GAP,
           f'Ответов: {known} из {total}', 17, MUTED)
    row_y = y + 72
    for index, (label, count) in enumerate(zip(labels, counts)):
        if not count:
            continue
        color = BEIGE if index == len(counts) - 1 else BROWN
        c.text(x+22, row_y, label, 18, MUTED, width=245)
        c.text(x+328, row_y, count, 19, color, 600, anchor='rt', width=55)
        c.d.rounded_rectangle((x+22, row_y + 24, x+328, row_y + 30), radius=3, fill='white')
        c.d.rounded_rectangle((x+22, row_y + 24, x+22 + max(6, 306 * count / total), row_y + 30), radius=3, fill=color)
        row_y += 38


def water_icon():
    # Flat vector silhouette, antialiased to match the approved brown poop asset.
    scale = 4
    image = Image.new('RGBA', (72 * scale, 88 * scale), (0, 0, 0, 0))
    curves = [((36, 2), (31, 18), (8, 36), (8, 54)),
              ((8, 54), (8, 72), (20, 84), (36, 84)),
              ((36, 84), (52, 84), (64, 72), (64, 54)),
              ((64, 54), (64, 36), (41, 18), (36, 2))]
    points = []
    for a, b, e, f in curves:
        for step in range(41):
            t = step / 40
            u = 1 - t
            points.append(tuple(scale * (u**3*a[i] + 3*u*u*t*b[i] + 3*u*t*t*e[i] + t**3*f[i]) for i in (0, 1)))
    ImageDraw.Draw(image).polygon(points, fill=WATER)
    return image.resize((54, 66), Image.Resampling.LANCZOS)


def estimates_height(total):
    return 180 + (132 if total > 0 else 0)


def draw_estimates(c, y, total, title):
    mass, water, _ = estimate_waste_metrics(total)
    c.text(48, y, title, 25, weight=500)
    for x in (48, 430):
        c.d.rounded_rectangle((x, y+42, x+362, y+158), radius=24, fill=PALE)
    poop = c.poop.resize((62, 60), Image.Resampling.LANCZOS)
    center_y = y + 100
    # Center the visible silhouettes, excluding transparent PNG margins.
    columns = []
    for icon, center_x, value, label, color in (
        (poop, 107, format_mass(mass).replace('.', ','), 'Общая масса кала', BROWN),
        (water_icon(), 489, f'{water:.0f} л', 'воды на смыв', WATER),
    ):
        icon = icon.crop(icon.getbbox())
        icon = icon.resize((round(icon.width * 64 / icon.height), 64), Image.Resampling.LANCZOS)
        icon_x = center_x - icon.width//2
        c.im.paste(icon, (icon_x, center_y - 32), icon)
        # Measure from the silhouette, ignoring the faint antialiased fringe.
        right_edge = icon.getchannel('A').point(lambda alpha: 255 if alpha >= 128 else 0).getbbox()[2]
        columns.append((icon_x + right_edge + 22, value, label, color))
    metric_row(c, columns, top=center_y-29, height=58, width=224, value_size=42, label_size=20)
    comparison = mass_comparison(mass / 1000)
    if comparison is not None:
        animal, label = comparison
        top = y + 180
        c.d.rounded_rectangle((48, top, 792, top+112), radius=24, fill=PALE)
        silhouette = animal_icon(animal.icon, 116, 80)
        c.im.paste(silhouette, (68+(116-silhouette.width)//2, top+(112-silhouette.height)//2), silhouette)
        reference = format_mass(animal.kg * 1000).replace('.', ',')
        lines = [(label, 34, BROWN, 650),
                 (f'По массе · {animal.name.lower()} ≈ {reference}', 20, MUTED, 400),
                 ('Силуэты: PhyloPic · Public domain', 11, MUTED, 400)]
        heights = [c.font(size, weight).getbbox(line, anchor='lt')[3] for line, size, _, weight in lines]
        text_y = top + (112 - sum(heights) - 2 * TEXT_GAP)//2
        for (line, size, color, weight), height in zip(lines, heights):
            c.text(210, text_y, line, size, color, weight, width=558)
            text_y += height + TEXT_GAP


def render_weekly(card):
    d = card.details
    if d is None or len(d.bristol) != 5 or len(d.feeling) != 4:
        raise ValueError('Weekly report details are required')
    total, previous, members = card.total, d.previous_total, len(card.participants)
    if sum(d.bristol) != total or sum(d.feeling) != total:
        raise ValueError('Weekly answer distributions must cover all marks')
    first_y = 578
    second_y = first_y + distribution_height(d.bristol) + 18
    bottom = max(752, second_y + distribution_height(d.feeling))
    if not card.private:
        first_y = 578 + participants_height(card) + (12 if card.participants else 0)
        second_y = first_y
        bottom = max(752, first_y + max(distribution_height(d.bristol), distribution_height(d.feeling)))
    extra = bottom - 752
    c = Canvas(812 + extra + estimates_height(total))
    c.logo(48, 24, 210, 53)
    if card.label:
        c.d.rounded_rectangle((500, 30, 792, 75), radius=23, fill=PALE)
        c.text(646, 43, card.label, 19, MUTED, width=260, anchor='mt')
    c.text(48, 101, 'Итоги недели', 40, weight=650)
    date_label = period_text(card.start, card.end)
    if c.d.textlength(date_label, font=c.font(27, 500)) <= 360:
        c.text(792, 112, date_label, 27, BROWN, 500, anchor='rt')
    elif ' – ' not in date_label:
        c.text(792, 112, date_label, 23, BROWN, 500, anchor='rt')
    else:
        first, last = date_label.split(' – ', 1)
        c.text(792, 100, first, 23, BROWN, 500, anchor='rt')
        c.text(792, 132, '– ' + last, 23, BROWN, 500, anchor='rt')
    c.calendar(card, 166, 200)
    c.d.rounded_rectangle((48, 302, 792, 438), radius=26, fill=PALE)
    unit = plural(total, 'отметка', 'отметки', 'отметок')
    value_size = 64
    available = 268 - c.font(24).getlength(unit) - 12
    while c.font(value_size, 650).getlength(str(total)) > available:
        value_size -= 1
    value_height = c.font(value_size, 650).getbbox(str(total), anchor='lt')[3]
    c.text(76, 320, total, value_size, BROWN, 650)
    unit_x = max(168, 76 + c.font(value_size, 650).getlength(str(total)) + 12)
    unit_height = c.font(24).getbbox(unit, anchor='lt')[3]
    c.text(unit_x, 320 + (value_height-unit_height)//2, unit, 24)
    c.text(76, 320 + value_height + TEXT_GAP, f'{total-previous:+d} к прошлой неделе',
           20, BROWN, 500, width=268)
    peak = max(total, previous, 1)
    for label, value, y, color in [('Прошлая неделя', previous, 322, BEIGE), ('Эта неделя', total, 382, BROWN)]:
        c.text(370, y, label, 21, MUTED)
        c.text(764, y, value, 23, BROWN, 600, anchor='rt', width=130)
        c.d.rounded_rectangle((370, y+30, 764, y+40), radius=5, fill='white')
        if value:
            c.d.rounded_rectangle((370, y+30, 370+max(10, 394*value/peak), y+40), radius=5, fill=color)
    for x in (48, 302, 556):
        c.d.rounded_rectangle((x, 458, x+236, 552), radius=23, fill=PALE)
    if card.private:
        first = (f'{total / 7:.1f}'.replace('.', ','), 'в среднем за день')
        last = (f'{d.previous_active_days}/7', 'дней неделей ранее')
    else:
        first = (members, plural(members, 'участник', 'участника', 'участников'))
        last = ('—', 'вне рейтинга чатов') if d.rank is None else (f'{d.rank}/{d.rank_total}', 'место среди чатов')
    baseline = metric_row(c, [(76, *first, BROWN), (330, f'{card.active_days}/7', 'дней с отметками', BROWN),
                              (584, *last, BROWN)],
                          top=458, height=94, width=180, value_size=38, label_size=19)
    if not card.private and d.rank is not None and d.previous_rank is not None and len(last[0]) <= 3:
        change = d.previous_rank - d.rank
        trend = 'без изменений' if not change else f'{change:+d} '+plural(abs(change), 'позиция', 'позиции', 'позиций')
        space = 180 - c.font(38, 650).getlength(last[0]) - 6
        c.text(764, baseline, trend, 16, BROWN, anchor='rs', width=space)
    if card.private:
        c.text(48, 578, 'Личная история' if card.private else 'Участники недели', 25, weight=650)
        shown = card.participants[:3]
        if not shown:
            c.text(48, 610, 'Пока нет отметок', 22, MUTED, width=362)
        peak = max((p.count for p in shown), default=1)
        for n, participant in enumerate(shown):
            y = 610 + n * 45
            c.text(48, y, participant.name, 22, width=235)
            c.text(410, y, participant.count, 23, BROWN, 600, anchor='rt', width=100)
            c.d.rounded_rectangle((48, y+29, 410, y+37), radius=4, fill=PALE)
            c.d.rounded_rectangle((48, y+29, 48+max(8, 362*participant.count/max(peak, 1)), y+37), radius=4, fill=BROWN)
        if len(card.participants) > 3:
            c.text(48, 755, f'Топ-3 из {members} · все в /stats', 17, MUTED, width=362)
    else:
        draw_participants(c, 578, card)
    distribution(c, first_y, 'Консистенция', d.bristol, ('Жёстко', 'Норма', 'Мягко', 'Водичка', 'Не указано'), x=442 if card.private else 48)
    distribution(c, second_y, 'Самочувствие', d.feeling, ('Прекрасно', 'Сойдёт', 'Ужасно', 'Не указано'))
    # Group rows grow by one compact row per two participants; private layout is unchanged.
    extra = bottom - 752
    draw_estimates(c, 780+extra, total, 'За неделю · примерно')
    footer = 780 + extra + estimates_height(total)
    c.d.line((48, footer, 792, footer), fill='#eee8e3')
    c.text(48, footer+14, 'Личная история' if card.private else 'Отметки этого чата', 16, MUTED)
    c.text(792, footer+14, '@poopcheck_bot', 16, MUTED, anchor='rt')
    return c.png()
