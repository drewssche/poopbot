"""Approved low daily calendar and visual weekly report, rendered without a browser."""
from datetime import timedelta
from io import BytesIO
import unicodedata
from PIL import Image, ImageDraw, ImageFont
from app.services.weekly_report_card import ASSETS, INK, BROWN, MUTED, BEIGE, PALE, period_text, plural
from app.services.stats_common import estimate_waste_metrics, format_mass
from app.services.report_animals import mass_comparison

WATER = '#3DA5FF'


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


def distribution_height(counts):
    return 78 if not sum(counts[:-1]) else 70 + 38 * sum(n > 0 for n in counts)


def distribution(c, y, title, counts, labels):
    height = distribution_height(counts)
    c.d.rounded_rectangle((442, y, 792, y + height), radius=20, fill=PALE)
    c.text(464, y + 12, title, 23, weight=650)
    total, known = sum(counts), sum(counts[:-1])
    if not known:
        value = 'Пока нет отметок' if not total else f'Нет ответов · {total} {plural(total, "отметка", "отметки", "отметок")}'
        c.text(464, y + 45, value, 19, MUTED, width=306)
        return
    c.text(464, y + 43, f'Ответов: {known} из {total}', 17, MUTED)
    row_y = y + 72
    for index, (label, count) in enumerate(zip(labels, counts)):
        if not count:
            continue
        color = BEIGE if index == len(counts) - 1 else BROWN
        c.text(464, row_y, label, 18, MUTED, width=245)
        c.text(770, row_y, count, 19, color, 600, anchor='rt', width=55)
        c.d.rounded_rectangle((464, row_y + 24, 770, row_y + 30), radius=3, fill='white')
        c.d.rounded_rectangle((464, row_y + 24, 464 + max(6, 306 * count / total), row_y + 30), radius=3, fill=color)
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
    c.im.paste(poop, (76, y+68), poop)
    drop = water_icon()
    c.im.paste(drop, (462, y+63), drop)
    c.text(162, y+62, format_mass(mass).replace('.', ','), 42, BROWN, 650, width=224)
    c.text(162, y+120, 'Общая каловая масса', 20, width=224)
    c.text(544, y+62, f'{water:.0f} л', 42, WATER, 650, width=224)
    c.text(544, y+120, 'воды на смыв', 22, width=224)
    comparison = mass_comparison(mass / 1000)
    if comparison is not None:
        animal, label = comparison
        top = y + 180
        c.d.rounded_rectangle((48, top, 792, top+112), radius=24, fill=PALE)
        silhouette = Image.open(ASSETS / f'animal-{animal.icon}.png').convert('RGBA')
        silhouette.thumbnail((116, 80), Image.Resampling.LANCZOS)
        c.im.paste(silhouette, (68+(116-silhouette.width)//2, top+(112-silhouette.height)//2), silhouette)
        c.text(210, top+18, label, 34, BROWN, 650, width=558)
        reference = format_mass(animal.kg * 1000).replace('.', ',')
        c.text(210, top+70, f'По массе · {animal.name.lower()} ≈ {reference}', 20, MUTED, width=558)
        c.text(210, top+97, 'Силуэты: PhyloPic · Public domain', 11, MUTED)


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
    c.text(76, 320, total, 64, BROWN, 650, width=230)
    c.text(168 if total < 100 else 76, 337 if total < 100 else 385,
           plural(total, 'отметка', 'отметки', 'отметок'), 24)
    # Large totals need their own line; keep comparison visible without collision.
    if total < 100:
        c.text(76, 401, f'{total-previous:+d} к прошлой неделе', 20, BROWN, 500, width=268)
    else:
        c.text(310, 408, f'{total-previous:+d}', 18, BROWN, 500, width=100, anchor='rt')
    peak = max(total, previous, 1)
    for label, value, y, color in [('Прошлая неделя', previous, 322, BEIGE), ('Эта неделя', total, 382, BROWN)]:
        c.text(370, y, label, 21, MUTED)
        c.text(764, y, value, 23, BROWN, 600, anchor='rt', width=130)
        c.d.rounded_rectangle((370, y+30, 764, y+40), radius=5, fill='white')
        if value:
            c.d.rounded_rectangle((370, y+30, 370+max(10, 394*value/peak), y+40), radius=5, fill=color)
    for x in (48, 302, 556):
        c.d.rounded_rectangle((x, 458, x+236, 552), radius=23, fill=PALE)
    c.text(76, 472, f'{total / 7:.1f}'.replace('.', ',') if card.private else members, 38, BROWN, 650, width=180)
    c.text(76, 525, 'в среднем за день' if card.private else plural(members, 'участник', 'участника', 'участников'), 19, width=180)
    c.text(330, 472, f'{card.active_days}/7', 38, BROWN, 650)
    c.text(330, 525, 'дней с отметками', 19)
    if card.private:
        c.text(584, 472, f'{d.previous_active_days}/7', 38, BROWN, 650)
        c.text(584, 525, 'дней неделей ранее', 18, width=180)
    elif d.rank is not None:
        c.text(584, 472, f'{d.rank}/{d.rank_total}', 38, BROWN, 650, width=180)
        if d.previous_rank is not None:
            change = d.previous_rank - d.rank
            trend = 'без изменений' if not change else f'{change:+d} '+plural(abs(change), 'позиция', 'позиции', 'позиций')
            # Do not place a trend over a long rank number.
            if len(f'{d.rank}/{d.rank_total}') <= 3:
                c.text(764, 487, trend, 16, BROWN, anchor='rt', width=109)
        c.text(584, 525, 'место среди чатов', 19, width=180)
    else:
        c.text(584, 472, '—', 38, BROWN, 650)
        c.text(584, 525, 'вне рейтинга чатов', 18, width=180)
    c.text(48, 578, 'Личная история' if card.private else 'Участники недели', 25, weight=650)
    shown = card.participants[:3]
    if not shown:
        c.text(48, 626, 'Пока нет отметок', 22, MUTED, width=362)
    peak = max((p.count for p in shown), default=1)
    for n, participant in enumerate(shown):
        y = 621 + n * 45
        c.text(48, y, participant.name, 22, width=235)
        c.text(410, y, participant.count, 23, BROWN, 600, anchor='rt', width=100)
        c.d.rounded_rectangle((48, y+29, 410, y+37), radius=4, fill=PALE)
        c.d.rounded_rectangle((48, y+29, 48+max(8, 362*participant.count/max(peak, 1)), y+37), radius=4, fill=BROWN)
    if len(card.participants) > 3:
        c.text(48, 755, f'Топ-3 из {members} · все в /stats', 17, MUTED, width=362)
    distribution(c, first_y, 'Консистенция', d.bristol, ('Жёстко', 'Норма', 'Мягко', 'Водичка', 'Не указано'))
    distribution(c, second_y, 'Самочувствие', d.feeling, ('Прекрасно', 'Сойдёт', 'Ужасно', 'Не указано'))
    # Canvas height and the footer depend only on answer blocks, not group size.
    extra = bottom - 752
    draw_estimates(c, 780+extra, total, 'За неделю · примерно')
    footer = 780 + extra + estimates_height(total)
    c.d.line((48, footer, 792, footer), fill='#eee8e3')
    c.text(48, footer+14, 'Личная история' if card.private else 'Отметки этого чата', 16, MUTED)
    c.text(792, footer+14, '@poopcheck_bot', 16, MUTED, anchor='rt')
    return c.png()
